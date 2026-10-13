import pytest
from app.accounting.domain import AccountingError
from app.business.storage import AttachmentStore
from app.import_wizard.service import ImportWizardService
from test_business_postgres import ADMIN_URL, APP_URL, business_postgres, business_case
from test_stage12_import_wizard import preview, CONTENT

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason='需要专用PostgreSQL管理员和受限运行连接')
TABLES = ('bank_import_files', 'bank_import_previews', 'bank_import_confirmations', 'bank_import_source_files', 'bank_import_profiles')


def test_pg_chinese_preview_native_lineage_and_repeat(business_case, tmp_path):
    c = business_case
    wizard = ImportWizardService(c.db, AttachmentStore(tmp_path), c.service)
    result, item = preview(wizard, c.finance, c.book)
    assert wizard.confirm(c.finance, c.book, result['id'], True)['imported'] == 2
    assert wizard.confirm(c.finance, c.book, result['id'])['replayed']
    assert wizard.profiles(c.reviewer, c.book)['profiles'][0]['bank_account'] == '工行-1234'
    assert wizard.original(c.reviewer, c.book, item['file_id'])['data'] == CONTENT.encode('utf-8')


def test_pg_import_force_rls_and_worker_no_grants(business_case, tmp_path):
    c = business_case
    wizard = ImportWizardService(c.db, AttachmentStore(tmp_path), c.service)
    result, item = preview(wizard, c.finance, c.book)
    wizard.confirm(c.finance, c.book, result['id'], True)
    with c.psycopg.connect(ADMIN_URL) as conn:
        for name in TABLES:
            enabled, forced = conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=%s::regclass", ('accounting.' + name,)).fetchone()
            assert enabled and forced
            assert not conn.execute("SELECT has_table_privilege('accounting_worker',%s,'SELECT')", ('accounting.' + name,)).fetchone()[0]
    for actor in (c.employee, c.unassigned):
        with c.db.transaction(actor=actor) as conn:
            for name in TABLES:
                assert conn.execute('SELECT * FROM ' + name).fetchall() == []
        with pytest.raises(AccountingError):
            wizard.original(actor, c.book, item['file_id'])


def test_pg_import_wrong_company_and_frozen_metadata_rejected(business_case, tmp_path):
    c = business_case
    wizard = ImportWizardService(c.db, AttachmentStore(tmp_path), c.service)
    result, _ = preview(wizard, c.finance, c.book)
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.finance) as conn:
            conn.execute("INSERT INTO bank_import_confirmations VALUES(?,?,?,?,?,?,'stamp')", (result['id'], c.book, c.owner.firm_id, c.other_company, '{}', c.finance.user_id))
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.finance) as conn:
            conn.execute("UPDATE bank_import_previews SET payload_json='{}' WHERE id=?", (result['id'],))


def test_pg_receipt_without_all_sources_and_lineage_cannot_bypass_confirmation(business_case, tmp_path):
    import json
    c = business_case
    wizard = ImportWizardService(c.db, AttachmentStore(tmp_path), c.service)
    result, _ = preview(wizard, c.finance, c.book)
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.db.transaction(write=True, actor=c.finance) as conn:
            conn.execute("INSERT INTO bank_import_confirmations VALUES(?,?,?,?,?,?,'stamp')", (result['id'], c.book, c.owner.firm_id, c.company, json.dumps({'preview_id': result['id'], 'book_id': c.book}), c.finance.user_id))
