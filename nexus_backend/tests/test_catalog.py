from period_fixtures import restore_period_state
import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountDefinition, AccountImportRequest, AccountUpdate, TemplateDefinition, TemplatePreviewRequest, TemplateUpdate
from app.accounting.database import Database
from app.accounting.domain import AccountingError, Actor
from app.accounting.models import BookCreate, ImportRequest, SourceRow, ApprovalRequest, DraftUpdate, ExportRequest
from app.accounting.seed import LOCAL_ACTOR, bootstrap
from app.accounting.service import AccountingService


@pytest.fixture
def case(tmp_path):
    accounting = AccountingService(Database(tmp_path / "catalog.db"))
    bootstrap(accounting, seed_examples=False)
    book = accounting.create_book(LOCAL_ACTOR, BookCreate(name="客户科目", period="2026-10"))["id"]
    return accounting, CatalogService(accounting.db), book


def template(**changes):
    value = dict(name="已付款办公费用", business_type="procurement_expense", direction="expense", category="office", payment_status="paid",
                 lines=[dict(account_code="6602.02", side="debit", amount_field="gross"), dict(account_code="1002", side="credit", amount_field="gross")])
    return TemplateDefinition(**{**value, **changes})


def payload(**changes):
    return dict(occurred_on="2026-10-01", description="办公采购", counterparty="真实供应商", amount="113.00", direction="expense", payment_status="paid", category="office", project="项目甲", **changes)


def render(case, business_type="procurement_expense", business_payload=None, **kwargs):
    accounting, catalog, book = case
    with accounting.db.transaction(actor=LOCAL_ACTOR) as conn:
        return catalog.generate_in_transaction(conn, LOCAL_ACTOR, book, business_type, business_payload or payload(), 11300, **kwargs)


def new_account(code="2221.01", **changes):
    return AccountDefinition(code=code, name="会计配置税额科目", category="liability", normal_balance="credit", **changes)


def make_draft(case):
    accounting, _, book = case
    accounting.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name="测试", rows=[SourceRow(external_id="source-01", date="2026-10-01", description="办公费用", counterparty="供应商", amount="113.00", direction="expense", evidence_ref="票据")]))
    accounting.suggestions(LOCAL_ACTOR, book)
    return accounting.get_book(LOCAL_ACTOR, book)["drafts"][0]


def test_client_chart_precise_metadata_and_no_auto_added_accounts(case):
    _, catalog, book = case
    result = catalog.create_account(LOCAL_ACTOR, book, new_account(auxiliary_requirements=["counterparty"]))
    assert result["version"] == 1 and result["auxiliary_requirements"] == ["counterparty"]
    assert len(catalog.get(LOCAL_ACTOR, book)["accounts"]) == 11
    with pytest.raises(AccountingError, match="存在"):
        catalog.create_account(LOCAL_ACTOR, book, new_account())


def test_account_stale_version_and_code_changes_are_rejected(case):
    _, catalog, book = case
    catalog.create_account(LOCAL_ACTOR, book, new_account())
    update = AccountUpdate(**new_account().model_dump(), expected_version=1)
    assert catalog.update_account(LOCAL_ACTOR, book, update.code, update)["version"] == 2
    with pytest.raises(AccountingError, match="版本"):
        catalog.update_account(LOCAL_ACTOR, book, update.code, update)
    with pytest.raises(AccountingError, match="代码"):
        catalog.update_account(LOCAL_ACTOR, book, "different", update)


def test_account_import_preview_does_not_write_and_commit_is_atomic(case):
    _, catalog, book = case
    request = AccountImportRequest(rows=[new_account().model_dump(), {**new_account("6401").model_dump(), "expected_version": 9}])
    with pytest.raises(AccountingError):
        catalog.import_accounts(LOCAL_ACTOR, book, request)
    assert not any(account["code"] == "2221.01" for account in catalog.get(LOCAL_ACTOR, book)["accounts"])
    request = AccountImportRequest(rows=[new_account().model_dump()])
    assert catalog.import_accounts(LOCAL_ACTOR, book, request, preview=True)["create_count"] == 1
    assert len(catalog.get(LOCAL_ACTOR, book)["accounts"]) == 10
    assert catalog.import_accounts(LOCAL_ACTOR, book, request)["count"] == 1


def test_in_use_auxiliary_requirements_cannot_rewrite_history(case):
    make_draft(case)
    _, catalog, book = case
    request = AccountUpdate(code="6602.02", name="办公费", auxiliary_requirements=["project"], expected_version=1)
    with pytest.raises(AccountingError, match="用于凭证"):
        catalog.update_account(LOCAL_ACTOR, book, request.code, request)


@pytest.mark.parametrize("changes", [{"code": "../secret"}, {"auxiliary_requirements": ["department", "department"]}, {"auxiliary_requirements": ["arbitrary"]}, {"active": 1}])
def test_chart_rejects_unbounded_or_ambiguous_fields(changes):
    with pytest.raises(ValidationError):
        AccountDefinition.model_validate({"code": "1234", "name": "科目", **changes})


@pytest.mark.parametrize("lines", [
    [dict(account_code="1002", side="debit", amount_field="gross"), dict(account_code="1002", side="credit", amount_field="net")],
    [dict(account_code="1002", side="debit", amount_field="gross"), dict(account_code="1002", side="debit", amount_field="gross"), dict(account_code="1002", side="credit", amount_field="gross"), dict(account_code="1002", side="credit", amount_field="gross")],
    [dict(account_code="1002", side="debit", amount_field="eval(amount)"), dict(account_code="1002", side="credit", amount_field="gross")],
])
def test_template_rejects_unbalanced_duplicated_and_executable_formula(lines):
    with pytest.raises(ValidationError):
        template(lines=lines)


def test_template_versions_are_immutable_and_generation_uses_latest(case):
    accounting, catalog, book = case
    first = catalog.save_template(LOCAL_ACTOR, book, template())
    second = catalog.save_template(LOCAL_ACTOR, book, TemplateUpdate(**template(name="会计修订的办公费用").model_dump(), expected_version=1), first["id"])
    assert second["version"] == 2
    generated = render(case)
    assert generated["template_version"] == 2
    assert generated["lines"] == [("6602.02", 11300, 0), ("1002", 0, 11300)]
    with accounting.db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM journal_template_versions").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE journal_template_versions SET definition='{}'")


def test_no_rule_returns_none_and_ambiguous_rules_require_accountant(case):
    _, catalog, book = case
    assert render(case) is None
    catalog.save_template(LOCAL_ACTOR, book, template())
    catalog.save_template(LOCAL_ACTOR, book, template(name="另一同条件规则"))
    with pytest.raises(AccountingError, match="同优先级"):
        render(case)


def test_template_can_be_retired_after_its_account_was_disabled(case):
    _, catalog, book = case
    created = catalog.save_template(LOCAL_ACTOR,book,template())
    catalog.update_account(LOCAL_ACTOR,book,'1002',AccountUpdate(code='1002',name='银行存款',active=False,expected_version=1))
    retired = catalog.save_template(LOCAL_ACTOR,book,TemplateUpdate(**template(active=False).model_dump(),expected_version=1),created['id'])
    assert retired['active'] is False and retired['version'] == 2
    assert render(case) is None


def test_tax_template_requires_verified_components_and_never_estimates_tax(case):
    _, catalog, book = case
    catalog.create_account(LOCAL_ACTOR, book, new_account())
    catalog.save_template(LOCAL_ACTOR, book, template(lines=[dict(account_code="6602.02", side="debit", amount_field="net"), dict(account_code="2221.01", side="debit", amount_field="tax"), dict(account_code="1002", side="credit", amount_field="gross")]))
    with pytest.raises(AccountingError, match="不能根据税率推测"):
        render(case)
    with pytest.raises(AccountingError, match="总额"):
        render(case, recognized={"net": "100", "tax": "12"})
    assert render(case, recognized={"net": "100", "tax": "13"})["lines"] == [("6602.02", 10000, 0), ("2221.01", 1300, 0), ("1002", 0, 11300)]


def test_auxiliary_missing_and_foreign_department_block_review(case):
    accounting, catalog, book = case
    catalog.update_account(LOCAL_ACTOR, book, "6602.02", AccountUpdate(code="6602.02", name="办公费", auxiliary_requirements=["department", "project"], expected_version=1))
    catalog.save_template(LOCAL_ACTOR, book, template())
    assert "部门" in render(case)["issues"][0]
    with accounting.db.transaction(write=True) as conn:
        company = conn.execute("SELECT company_id FROM books WHERE id=?", (book,)).fetchone()[0]
        conn.execute("INSERT INTO departments(id,company_id,name) VALUES('own-department',?,'本企业部门')", (company,))
    assert render(case, context={"department_id": "own-department"})["issues"] == []
    assert render(case, context={"department_id": "other-department"})["issues"]


def test_auxiliary_receipt_frozen_after_approval(case):
    accounting, catalog, book = case
    draft = make_draft(case)
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        catalog.save_auxiliaries_in_transaction(conn, LOCAL_ACTOR, book, draft["id"], [{"counterparty": "供应商"}, {}])
        assert catalog.load_auxiliaries_in_transaction(conn, book, draft["id"])[0] == {"counterparty": "供应商"}
    accounting.approve(LOCAL_ACTOR, book, draft["id"], ApprovalRequest(expected_version=1))
    with accounting.db.transaction(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM draft_line_auxiliaries WHERE draft_id=?", (draft["id"],))


def test_closed_period_blocks_catalog_and_template_generation(case):
    accounting, catalog, book = case
    catalog.save_template(LOCAL_ACTOR, book, template())
    with accounting.db.transaction(write=True) as conn:
        restore_period_state(catalog.db, book, conn=conn)
    with pytest.raises(AccountingError, match="关闭"):
        catalog.create_account(LOCAL_ACTOR, book, new_account())
    with pytest.raises(AccountingError, match="关闭"):
        render(case)


def test_book_editor_reads_templates_but_cannot_publish(case):
    accounting, catalog, book = case
    editor = Actor("catalog-editor", LOCAL_ACTOR.firm_id)
    with accounting.db.transaction(write=True) as conn:
        conn.execute("INSERT INTO users(id,name) VALUES(?,?)", (editor.user_id, "录入人员"))
        conn.execute("INSERT INTO memberships VALUES(?,?,'operator')", (editor.firm_id, editor.user_id))
        conn.execute("INSERT INTO book_access(book_id,firm_id,user_id,role) VALUES(?,?,?,'editor')", (book, editor.firm_id, editor.user_id))
    assert catalog.get(editor, book)["can_manage"] is False
    with pytest.raises(AccountingError):
        catalog.save_template(editor, book, template())
    foreign = Actor("catalog-editor", "foreign-firm")
    with pytest.raises(AccountingError):
        catalog.get(foreign, book)


def test_preview_is_exact_and_never_saves_configuration(case):
    _, catalog, book = case
    request = TemplatePreviewRequest(template=template(), payload=payload())
    result = catalog.preview_template(LOCAL_ACTOR, book, request)
    assert result["lines"][0]["debit"] == "113.00"
    assert catalog.get(LOCAL_ACTOR, book)["templates"] == []


def test_sqlite_direct_closed_chart_and_auxiliary_writes_are_rejected(case):
    accounting, catalog, book = case
    draft = make_draft(case)
    with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        catalog.save_auxiliaries_in_transaction(conn, LOCAL_ACTOR, book, draft['id'], [{'counterparty':'供应商'},{}])
        restore_period_state(catalog.db, book, conn=conn)
    commands = [
        ("INSERT INTO accounts(book_id,code,name) VALUES(?,'9999','关闭期间不得新建')", (book,)),
        ("UPDATE accounts SET name='关闭期间不得改名' WHERE book_id=? AND code='1002'", (book,)),
        ("DELETE FROM draft_line_auxiliaries WHERE draft_id=?", (draft['id'],)),
        ("UPDATE draft_line_auxiliaries SET values_json='{}' WHERE draft_id=?", (draft['id'],)),
    ]
    for command, args in commands:
        with pytest.raises(sqlite3.IntegrityError, match='open period'):
            with accounting.db.transaction(write=True) as conn:
                conn.execute(command, args)


def test_sqlite_direct_account_identity_and_used_auxiliary_change_are_rejected(case):
    accounting, _, book = case
    make_draft(case)
    commands = [
        ("UPDATE accounts SET code='changed' WHERE book_id=? AND code='1002'", (book,)),
        ("UPDATE accounts SET auxiliary_requirements='[\"project\"]' WHERE book_id=? AND code='6602.02'", (book,)),
    ]
    for command, args in commands:
        with pytest.raises(sqlite3.IntegrityError, match='immutable'):
            with accounting.db.transaction(write=True) as conn:
                conn.execute(command, args)


def test_accounting_person_fact_survives_authorization_removal_and_exports(case):
    accounting, catalog, book = case
    catalog.update_account(LOCAL_ACTOR, book, '6602.02', AccountUpdate(code='6602.02', name='办公费', expected_version=1, auxiliary_requirements=['employee']))
    with accounting.db.transaction(write=True) as conn:
        company = conn.execute("SELECT company_id FROM books WHERE id=?", (book,)).fetchone()[0]
        conn.execute("INSERT INTO users(id,name) VALUES('historical-worker','原企业员工')")
        conn.execute("INSERT INTO company_memberships(company_id,user_id,role) VALUES(?,'historical-worker','employee')", (company,))
    draft = make_draft(case)
    lines = [{**line, 'auxiliary_values': {'employee':'historical-worker'} if line['account_code']=='6602.02' else {}} for line in draft['lines']]
    updated = accounting.update_draft(LOCAL_ACTOR, book, draft['id'], DraftUpdate(expected_version=1, description=draft['description'], lines=lines))
    assert updated['issues'] == []
    with accounting.db.transaction(write=True) as conn:
        conn.execute("DELETE FROM company_memberships WHERE user_id='historical-worker' AND company_id=?", (company,))
        conn.execute("UPDATE users SET name='改名后的账号',active=0 WHERE id='historical-worker'")
    reviewed = accounting.approve(LOCAL_ACTOR, book, draft['id'], ApprovalRequest(expected_version=2))
    assert reviewed['issues'] == [] and reviewed['lines'][0]['auxiliary_values']['employee'] == 'historical-worker'
    receipt = accounting.export(LOCAL_ACTOR, book, ExportRequest(draft_ids=[draft['id']], idempotency_key='historical-worker-export'))
    assert 'historical-worker' in receipt['csv']
    with accounting.db.transaction() as conn:
        fact = conn.execute("SELECT display_name FROM catalog_auxiliary_people WHERE book_id=? AND employee_id='historical-worker'", (book,)).fetchone()
        assert fact['display_name'] == '原企业员工'
        assert conn.execute("SELECT count(*) FROM company_memberships WHERE user_id='historical-worker'").fetchone()[0] == 0
    with pytest.raises(AccountingError):
        catalog.get(Actor('historical-worker', LOCAL_ACTOR.firm_id), book)


def test_foreign_accounting_person_not_adopted_and_dictionary_is_immutable(case):
    accounting, catalog, book = case
    other = accounting.create_book(LOCAL_ACTOR, BookCreate(name='外部企业人员', period='2026-10'))
    with accounting.db.transaction(write=True) as conn:
        conn.execute("INSERT INTO users(id,name) VALUES('foreign-worker','外企员工')")
        conn.execute("INSERT INTO company_memberships(company_id,user_id,role) VALUES(?,'foreign-worker','employee')", (other['company_id'],))
    draft = make_draft(case)
    with pytest.raises(AccountingError, match='归属证明'):
        with accounting.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
            catalog.save_auxiliaries_in_transaction(conn, LOCAL_ACTOR, book, draft['id'], [{'employee':'foreign-worker'},{}])
    with accounting.db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM catalog_auxiliary_people WHERE book_id=?", (book,)).fetchone()[0] == 0
    with pytest.raises(sqlite3.IntegrityError, match='ownership proof'):
        with accounting.db.transaction(write=True) as conn:
            conn.execute("INSERT INTO catalog_auxiliary_people VALUES(?,?,?,?,?,?,?)", (book, LOCAL_ACTOR.firm_id, accounting.get_book(LOCAL_ACTOR, book)['book']['company_id'], 'foreign-worker','伪造人员',LOCAL_ACTOR.user_id,'2026-10-12'))
