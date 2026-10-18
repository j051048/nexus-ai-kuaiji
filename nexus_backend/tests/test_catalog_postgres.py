"""Real SQL checks, independent of API validation; root supplies test DSNs."""
import json

import pytest

from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountUpdate, TemplateUpdate
from app.accounting.models import ImportRequest, SourceRow, DraftUpdate, ApprovalRequest, ExportRequest
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres, full_event, handoff
from test_catalog import new_account, template

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员和受限运行测试连接")


def test_catalog_force_rls_and_no_worker_or_public_table_grants(business_case):
    case = business_case
    with case.psycopg.connect(ADMIN_URL) as conn:
        tables = ('journal_templates', 'journal_template_versions', 'draft_line_auxiliaries')
        rows = conn.execute("SELECT relname,relrowsecurity,relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='accounting' AND relname=ANY(%s)", (list(tables),)).fetchall()
        assert len(rows) == 3 and all(enabled and forced for _, enabled, forced in rows)
        for table in tables:
            assert not conn.execute("SELECT has_table_privilege('accounting_worker',%s,'SELECT')", ('accounting.' + table,)).fetchone()[0]


def test_sql_template_catalog_not_visible_to_employee_or_other_company(business_case):
    case = business_case
    catalog = CatalogService(case.db)
    item = catalog.save_template(case.owner, case.book, template())
    other = catalog.save_template(case.owner, case.other_book, template(name="其他企业模板"))
    for actor in (case.employee, case.unassigned):
        with case.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT * FROM journal_templates WHERE id=?", (item['id'],)).fetchall() == []
            assert conn.execute("SELECT * FROM journal_template_versions WHERE template_id=?", (item['id'],)).fetchall() == []
    with case.db.transaction(actor=case.reviewer) as conn:
        assert conn.execute("SELECT id FROM journal_templates WHERE id=?", (item['id'],)).fetchone()
        assert conn.execute("SELECT * FROM journal_templates WHERE id=?", (other['id'],)).fetchall() == []


def test_finance_editor_consumes_templates_but_cannot_publish_chart_sql(business_case):
    case = business_case
    CatalogService(case.db).save_template(case.owner, case.book, template())
    with case.db.transaction(actor=case.finance) as conn:
        assert conn.execute("SELECT * FROM journal_templates WHERE book_id=?", (case.book,)).fetchone()
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.finance) as conn:
            conn.execute("INSERT INTO accounts(book_id,code,name) VALUES(?,'9999','编辑人越权配置')", (case.book,))
    with case.db.transaction(write=True, actor=case.finance) as conn:
        assert conn.execute("UPDATE accounts SET name='越权改名' WHERE book_id=? AND code='1002'", (case.book,)).rowcount == 0


def test_template_history_cannot_be_rewritten_or_deleted_sql(business_case):
    case = business_case
    catalog = CatalogService(case.db)
    item = catalog.save_template(case.owner, case.book, template())
    revised = catalog.save_template(case.owner, case.book, TemplateUpdate(**template(name="第二版本").model_dump(), expected_version=1), item['id'])
    assert revised['version'] == 2
    # Runtime has no UPDATE grant at all. Either that boundary or the immutable
    # trigger may reject, and the historical definition must remain unchanged.
    with pytest.raises(case.psycopg.Error) as rejected:
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("UPDATE journal_template_versions SET definition='{}' WHERE template_id=?", (item['id'],))
    assert rejected.value.sqlstate in {'42501', 'P0001'}
    with case.db.transaction(actor=case.owner) as conn:
        history = conn.execute("SELECT definition FROM journal_template_versions WHERE template_id=? AND version=1", (item['id'],)).fetchone()[0]
        assert json.loads(history)['name'] == template().name
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("DELETE FROM journal_template_versions WHERE template_id=?", (item['id'],))


def test_auxiliary_scope_validation_does_not_reveal_or_accept_foreign_company(business_case):
    case = business_case
    catalog = CatalogService(case.db)
    catalog.update_account(case.owner, case.book, '6602.02', AccountUpdate(code='6602.02', name='办公费', expected_version=1, auxiliary_requirements=['department','employee']))
    with case.db.transaction(actor=case.reviewer) as conn:
        lines = [{'account_code':'6602.02','auxiliary_values':{'department':case.department,'employee':case.employee.user_id}}]
        assert catalog.validate_auxiliaries_in_transaction(conn, case.reviewer, case.book, lines) == []
        lines[0]['auxiliary_values']['department'] = 'foreign-department'
        assert catalog.validate_auxiliaries_in_transaction(conn, case.reviewer, case.book, lines)
        assert not conn.execute("SELECT accounting.catalog_aux_valid(?,'employee',?)", (case.other_book, case.employee.user_id)).fetchone()[0]


def test_auxiliary_values_frozen_when_draft_approved_sql(business_case):
    case = business_case
    event_id = full_event(case)
    _, draft_id = handoff(case, event_id)
    with case.db.transaction(write=True, actor=case.finance) as conn:
        # Balanced lines are required by the database review boundary.
        conn.execute("INSERT INTO draft_lines VALUES(?,?,0,'1002',1234,0)", (draft_id, case.book))
        conn.execute("INSERT INTO draft_lines VALUES(?,?,1,'2202',0,1234)", (draft_id, case.book))
        conn.execute("INSERT INTO draft_line_auxiliaries VALUES(?,?,0,?)", (draft_id, case.book, json.dumps({'counterparty':'供应商'})))
    with case.db.transaction(write=True, actor=case.owner) as conn:
        conn.execute("UPDATE drafts SET status='approved',approved_by=?,approved_at='2026-10-12T00:00:00+00:00' WHERE id=?", (case.owner.user_id, draft_id))
    with pytest.raises(case.psycopg.Error, match="draft and open period"):
        with case.db.transaction(write=True, actor=case.owner) as conn:
            conn.execute("DELETE FROM draft_line_auxiliaries WHERE draft_id=?", (draft_id,))


def test_sql_closed_period_cannot_receive_accounts_or_auxiliary_changes(business_case):
    case = business_case
    event_id = full_event(case)
    _, draft_id = handoff(case, event_id)
    with case.db.transaction(write=True, actor=case.finance) as conn:
        conn.execute("INSERT INTO draft_lines VALUES(?,?,0,'1002',0,1234)", (draft_id, case.book))
        conn.execute("INSERT INTO draft_line_auxiliaries VALUES(?,?,0,?)", (draft_id, case.book, json.dumps({'counterparty':'供应商'})))
    # Close through the admin fixture so this independent SQL boundary check
    # does not depend on the application's pending-draft close policy.
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("UPDATE accounting.periods SET status='closed' WHERE book_id=%s", (case.book,))
    commands = [
        ("INSERT INTO accounts(book_id,code,name) VALUES(?,'9999','关闭期间不可创建')", (case.book,)),
        ("UPDATE accounts SET name='关闭期间不可改名' WHERE book_id=? AND code='1002'", (case.book,)),
        ("DELETE FROM draft_line_auxiliaries WHERE draft_id=?", (draft_id,)),
        ("UPDATE draft_line_auxiliaries SET values_json='{}' WHERE draft_id=?", (draft_id,)),
    ]
    for command, args in commands:
        with pytest.raises(case.psycopg.Error, match='open period'):
            with case.db.transaction(write=True, actor=case.owner) as conn:
                conn.execute(command, args)


def test_proven_accounting_employee_outlives_membership_without_granting_access(business_case):
    case = business_case
    catalog = CatalogService(case.db)
    catalog.update_account(case.owner,case.book,'6602.02',AccountUpdate(code='6602.02',name='办公费',expected_version=1,auxiliary_requirements=['employee']))
    case.service.import_sources(case.owner,case.book,ImportRequest(source_name='人员事实验证',rows=[SourceRow(external_id='employee-fact',date='2026-10-01',description='办公费',counterparty='供应商',amount='113.00',direction='expense',evidence_ref='已归档')]))
    case.service.suggestions(case.owner,case.book)
    draft = case.service.get_book(case.owner,case.book)['drafts'][0]
    lines = [{**line,'auxiliary_values':{'employee':case.employee.user_id} if line['account_code']=='6602.02' else {}} for line in draft['lines']]
    case.service.update_draft(case.owner,case.book,draft['id'],DraftUpdate(expected_version=1,description=draft['description'],lines=lines))
    with case.db.transaction(write=True,actor=case.owner) as conn:
        conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?",(case.company,case.employee.user_id))
    reviewed = case.service.approve(case.owner,case.book,draft['id'],ApprovalRequest(expected_version=2))
    assert reviewed['issues'] == []
    receipt = case.service.export(case.owner,case.book,ExportRequest(draft_ids=[draft['id']],idempotency_key='historical-person-export'))
    assert case.employee.user_id in receipt['csv']
    with case.db.transaction(actor=case.employee) as conn:
        assert conn.execute("SELECT * FROM catalog_auxiliary_people WHERE book_id=?",(case.book,)).fetchall() == []
    with case.db.transaction(actor=case.owner) as conn:
        assert conn.execute("SELECT * FROM catalog_auxiliary_people WHERE book_id=? AND employee_id=?",(case.book,case.employee.user_id)).fetchone()
        assert not conn.execute("SELECT 1 FROM company_memberships WHERE company_id=? AND user_id=?",(case.company,case.employee.user_id)).fetchone()


def test_scoped_recorder_rejects_foreign_person_and_raw_sql_cannot_forge_identity(business_case):
    case = business_case
    with case.db.transaction(write=True,actor=case.owner) as conn:
        assert not conn.execute("SELECT accounting.record_catalog_employee(?,?)",(case.other_book,case.employee.user_id)).fetchone()[0]
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True,actor=case.owner) as conn:
            conn.execute("INSERT INTO catalog_auxiliary_people VALUES(?,?,?,?,?,?,?)",(case.other_book,case.owner.firm_id,case.other_company,case.employee.user_id,'伪造员工',case.owner.user_id,'2026-10-12'))
    with case.db.transaction(write=True,actor=case.finance) as conn:
        assert conn.execute("SELECT accounting.record_catalog_employee(?,?)",(case.book,case.employee.user_id)).fetchone()[0]
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with case.db.transaction(write=True,actor=case.owner) as conn:
            conn.execute("DELETE FROM catalog_auxiliary_people WHERE book_id=?",(case.book,))
    with case.db.transaction(write=True,actor=case.viewer) as conn:
        assert not conn.execute("SELECT accounting.record_catalog_employee(?,?)",(case.book,case.colleague.user_id)).fetchone()[0]


def test_mapped_business_applicant_is_historical_company_proof_after_removal(business_case):
    case = business_case
    event_id = full_event(case)
    handoff(case,event_id)
    with case.db.transaction(write=True,actor=case.owner) as conn:
        conn.execute("DELETE FROM company_memberships WHERE company_id=? AND user_id=?",(case.company,case.employee.user_id))
        assert conn.execute("SELECT accounting.catalog_aux_valid(?,'employee',?)",(case.book,case.employee.user_id)).fetchone()[0]
        assert conn.execute("SELECT accounting.record_catalog_employee(?,?)",(case.book,case.employee.user_id)).fetchone()[0]
        assert not conn.execute("SELECT accounting.catalog_aux_valid(?,'employee',?)",(case.other_book,case.employee.user_id)).fetchone()[0]
