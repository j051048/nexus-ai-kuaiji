from uuid import uuid4
import pytest
from app.accounting.bank_imports import BankImportService
from app.accounting.domain import AccountingError
from app.accounting.models import ApprovalRequest
from app.accounting.seed import LOCAL_ACTOR
from app.dispositions.models import SameTransaction
from app.dispositions.service import SourceDispositionService
from test_accounting import service, book, add_actor
from test_stage9_bank_imports import statement


def duplicates(service, book):
    service.import_sources(LOCAL_ACTOR, book, statement())
    service.import_sources(LOCAL_ACTOR, book, statement(external='display-2', stable='other-reference', namespace='second-feed'))
    service.suggestions(LOCAL_ACTOR, book)
    draft = service.get_book(LOCAL_ACTOR, book)['drafts'][0]
    check = BankImportService(service.db).overview(LOCAL_ACTOR, book, draft['source_id'])
    candidate = check['candidates'][0]
    request = SameTransaction(expected_version=draft['version'], source_fingerprint=check['fingerprint'], canonical_fingerprint=candidate['fingerprint'],
        reason='银行原回单及付款凭据证实两份来源同一交易', idempotency_key=uuid4().hex)
    return draft, candidate, request


def test_duplicate_isolation_is_append_only_stable_and_old_sql_cannot_mutate(service, book):
    draft, candidate, request = duplicates(service, book)
    guard = SourceDispositionService(service.db)
    receipt = guard.confirm(LOCAL_ACTOR, book, draft['source_id'], candidate['id'], request)
    assert receipt['inactive'] and not receipt['duplicate']
    assert guard.confirm(LOCAL_ACTOR, book, draft['source_id'], candidate['id'], request)['duplicate']
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert conn.execute('SELECT COUNT(*) FROM sources WHERE book_id=?', (book,)).fetchone()[0] == 2
        assert conn.execute('SELECT COUNT(*) FROM drafts WHERE book_id=?', (book,)).fetchone()[0] == 2
        source = conn.execute('SELECT * FROM sources WHERE id=?', (draft['source_id'],)).fetchone()
        assert guard.blocking_issues_in_transaction(conn, LOCAL_ACTOR, source)
    for sql in ('UPDATE drafts SET description=\'掩盖原记录\' WHERE id=?', 'DELETE FROM draft_lines WHERE draft_id=?'):
        with pytest.raises(Exception, match='isolated'):
            with service.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
                conn.execute(sql, (draft['id'],))


def test_reviewer_stale_version_wrong_scope_and_reversed_isolation_rejected(service, book):
    draft, candidate, request = duplicates(service, book)
    guard = SourceDispositionService(service.db)
    editor = add_actor(service, user_id='disposition-editor', firm_id=LOCAL_ACTOR.firm_id, role='operator', book_id=book)
    with pytest.raises(AccountingError):
        guard.confirm(editor, book, draft['source_id'], candidate['id'], request)
    with pytest.raises(AccountingError, match='变化'):
        guard.confirm(LOCAL_ACTOR, book, draft['source_id'], candidate['id'], request.model_copy(update={'expected_version':request.expected_version+1}))
    guard.confirm(LOCAL_ACTOR, book, draft['source_id'], candidate['id'], request)
    reverse = request.model_copy(update={'source_fingerprint': request.canonical_fingerprint, 'canonical_fingerprint':request.source_fingerprint, 'idempotency_key':uuid4().hex})
    with pytest.raises(AccountingError, match='隔离'):
        guard.confirm(LOCAL_ACTOR, book, candidate['id'], draft['source_id'], reverse)


def test_approved_duplicate_cannot_be_hidden_and_current_source_truth_required(service, book):
    draft, candidate, request = duplicates(service, book)
    guard = SourceDispositionService(service.db)
    with service.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        conn.execute("UPDATE drafts SET status='approved',version=version+1 WHERE id=?", (draft['id'],))
    with pytest.raises(AccountingError, match='冻结'):
        guard.confirm(LOCAL_ACTOR, book, draft['source_id'], candidate['id'], request)
    with pytest.raises(AccountingError):
        guard.confirm(LOCAL_ACTOR, book, candidate['id'], candidate['id'], request)


def test_original_bank_case_must_finish_before_isolation(service, book):
    from app.business.service import BusinessService
    from app.cases.service import CaseService
    from app.cases.models import CaseCreate, CaseCommand
    draft, candidate, request = duplicates(service, book)
    cases = CaseService(service.db, BusinessService(service.db))
    case = cases.create(LOCAL_ACTOR, CaseCreate(book_id=book,draft_id=draft['id'],kind='exception',
        title='原银行回单待核实',reason='需先完结原回单资料核验',idempotency_key=uuid4().hex))
    guard = SourceDispositionService(service.db)
    assert not guard.overview(LOCAL_ACTOR,book,draft['source_id'])['can_isolate']
    with pytest.raises(AccountingError,match='未关闭'):
        guard.confirm(LOCAL_ACTOR,book,draft['source_id'],candidate['id'],request)
    cases.command(LOCAL_ACTOR,case['id'],'close',CaseCommand(expected_version=case['version'],reason='核实回单并准备隔离重复来源',idempotency_key=uuid4().hex))
    assert guard.confirm(LOCAL_ACTOR,book,draft['source_id'],candidate['id'],request)['inactive']
