from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from app.accounting.bank_imports import BankImportService, DuplicateDistinction
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest, BookCreate, ExportRequest, ImportRequest
from app.accounting.seed import LOCAL_ACTOR
from test_accounting import service, book, row, add_actor


def statement(external='display-1', stable='bank-transaction-1', namespace='statement', account='bank-last1234', **facts):
    return ImportRequest(source_name='银行文件.csv', namespace=namespace, bank_account=account,
                         rows=[row(external_id=external, **facts).model_copy(update={'stable_transaction_id': stable})])


def test_legacy_hash_and_normalized_reimport_remain_compatible(service, book):
    original = ImportRequest(source_name='old', rows=[row()])
    service.import_sources(LOCAL_ACTOR, book, original)
    assert service.import_sources(LOCAL_ACTOR, book, original) == {'imported': 0, 'duplicates': 1}
    from app.accounting.service import fingerprint
    with service.db.transaction() as conn:
        assert conn.execute('SELECT fingerprint FROM sources').fetchone()[0] == fingerprint(row().model_dump(exclude={'stable_transaction_id'}))


def test_stable_identity_survives_display_id_and_filename_changes(service, book):
    assert service.import_sources(LOCAL_ACTOR, book, statement())['imported'] == 1
    assert service.import_sources(LOCAL_ACTOR, book, statement(external='changed-display')) == {'imported': 0, 'duplicates': 1}
    source = service.get_book(LOCAL_ACTOR, book)['sources'][0]
    assert source['external_id'] == 'display-1'
    assert BankImportService(service.db).overview(LOCAL_ACTOR, book, source['id'])['identity']['stable_transaction_id'] == 'bank-transaction-1'


def test_same_display_id_different_accounts_is_not_silently_merged(service, book):
    service.import_sources(LOCAL_ACTOR, book, statement())
    service.import_sources(LOCAL_ACTOR, book, statement(account='other-bank4567'))
    assert len(service.get_book(LOCAL_ACTOR, book)['sources']) == 2


def test_conflicting_stable_identity_rolls_back_every_new_row(service, book):
    service.import_sources(LOCAL_ACTOR, book, statement())
    request = statement(amount='1.00')
    request.rows.insert(0, row(external_id='first-new').model_copy(update={'stable_transaction_id': 'new-transaction'}))
    with pytest.raises(AccountingError, match='整批导入已回滚'):
        service.import_sources(LOCAL_ACTOR, book, request)
    assert len(service.get_book(LOCAL_ACTOR, book)['sources']) == 1


def test_same_company_second_book_cannot_duplicate_stable_transaction(service, book):
    company_id = service.get_book(LOCAL_ACTOR, book)['book']['company_id']
    second = service.create_book(LOCAL_ACTOR, BookCreate(name='同企业其他账套', period='2026-10', company_id=company_id))['id']
    service.import_sources(LOCAL_ACTOR, book, statement())
    with pytest.raises(AccountingError, match='该企业同一银行交易已有记录'):
        service.import_sources(LOCAL_ACTOR, second, statement())
    assert not service.get_book(LOCAL_ACTOR, second)['sources']


def test_near_duplicates_require_explicit_review_without_deleting_sources(service, book):
    service.import_sources(LOCAL_ACTOR, book, statement())
    # A different source namespace cannot hide identical records for the account.
    service.import_sources(LOCAL_ACTOR, book, statement(external='display-2', stable='transaction-2', namespace='aggregator'))
    service.suggestions(LOCAL_ACTOR, book)
    detail = service.get_book(LOCAL_ACTOR, book)
    drafts = detail['drafts']
    assert all(any('独立交易' in issue for issue in draft['issues']) for draft in drafts)
    with pytest.raises(AccountingError):
        service.approve(LOCAL_ACTOR, book, drafts[0]['id'], ApprovalRequest(expected_version=1))
    checks = BankImportService(service.db)
    view = checks.overview(LOCAL_ACTOR, book, drafts[0]['source_id'])
    other = view['candidates'][0]
    request = DuplicateDistinction(source_fingerprint=view['fingerprint'], other_fingerprint=other['fingerprint'], expected_version=1, reason='已核对两份不同银行原始付款回执')
    assert checks.distinguish(LOCAL_ACTOR, book, view['source_id'], other['id'], request) == {'distinct': True, 'duplicate': False}
    assert checks.distinguish(LOCAL_ACTOR, book, view['source_id'], other['id'], request)['duplicate']
    current = service.get_draft(LOCAL_ACTOR, book, drafts[0]['id'])
    assert current['version'] == 2 and current['issues'] == []
    with pytest.raises(AccountingError, match='版本'):
        service.approve(LOCAL_ACTOR, book, current['id'], ApprovalRequest(expected_version=1))
    service.approve(LOCAL_ACTOR, book, current['id'], ApprovalRequest(expected_version=2))
    assert service.export(LOCAL_ACTOR, book, ExportRequest(draft_ids=[current['id']], idempotency_key='distinct-only-after-review'))['count'] == 1
    assert len(service.get_book(LOCAL_ACTOR, book)['sources']) == 2


def test_editor_and_unassigned_book_cannot_distinguish(service, book):
    service.import_sources(LOCAL_ACTOR, book, statement())
    service.import_sources(LOCAL_ACTOR, book, statement(stable='second'))
    service.suggestions(LOCAL_ACTOR, book)
    draft = service.get_book(LOCAL_ACTOR, book)['drafts'][0]
    checks = BankImportService(service.db)
    view = checks.overview(LOCAL_ACTOR, book, draft['source_id'])
    other = view['candidates'][0]
    request = DuplicateDistinction(source_fingerprint=view['fingerprint'], other_fingerprint=other['fingerprint'], expected_version=1, reason='不能让经办员自行绕过重复风险')
    for actor in (add_actor(service, user_id='editor', firm_id=LOCAL_ACTOR.firm_id, role='operator', book_id=book), add_actor(service)):
        with pytest.raises(AccountingError):
            checks.distinguish(actor, book, draft['source_id'], other['id'], request)


def test_concurrent_same_bank_identity_creates_one_record(service, book):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.import_sources(LOCAL_ACTOR, book, statement()), range(2)))
    assert sum(value['imported'] for value in results) == 1
    assert sum(value['duplicates'] for value in results) == 1


def test_identity_requires_paired_namespace_and_account():
    with pytest.raises(ValidationError):
        statement(account='')
    with pytest.raises(ValidationError):
        statement(namespace='', account='')
