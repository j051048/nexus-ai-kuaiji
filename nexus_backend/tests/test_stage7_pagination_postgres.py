"""Summary paging and missing auxiliary flags under actual PostgreSQL RLS."""
import pytest

from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountDefinition
from app.accounting.models import BookCreate, DraftUpdate, ImportRequest
from test_accounting import row
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark=pytest.mark.skipif(not ADMIN_URL or not APP_URL,reason="需要专用 PostgreSQL 管理员与受限 API 测试连接")


def test_pg_workbench_paging_search_escaping_and_single_draft(business_case):
    case=business_case
    case.service.import_sources(case.owner,case.book,ImportRequest(source_name="合成分页",rows=[row(external_id=f"pg-page-{i:02}") for i in range(23)]))
    case.service.suggestions(case.owner,case.book)
    first=case.service.get_book(case.owner,case.book,limit=20,summaries=True)
    second=case.service.get_book(case.owner,case.book,limit=20,offset=20,summaries=True)
    assert len(first["drafts"])==20 and len(second["drafts"])==3
    assert not {d["id"] for d in first["drafts"]}&{d["id"] for d in second["drafts"]}
    assert case.service.get_draft(case.owner,case.book,first["drafts"][0]["id"])["detail_loaded"]
    assert case.service.workbench(case.owner,search="%")["pagination"]["total"]==0
    assert case.service.workbench(case.owner)["stats"]["pending_count"]==23
    assert case.service.book_choices(case.owner,case.company,"2026-10")["books"][0]["id"]==case.book


def test_pg_missing_auxiliary_is_visible_in_exception_summary(business_case):
    case=business_case
    CatalogService(case.db).create_account(case.owner,case.book,AccountDefinition(code="6602.97",name="项目费用",auxiliary_requirements=["project"]))
    case.service.import_sources(case.owner,case.book,ImportRequest(source_name="合成辅助",rows=[row()]))
    case.service.suggestions(case.owner,case.book)
    value=case.service.get_book(case.owner,case.book)["drafts"][0]
    lines=[{**line,"account_code":"6602.97" if line["debit"]!="0.00" else line["account_code"]} for line in value["lines"]]
    changed=case.service.update_draft(case.owner,case.book,value["id"],DraftUpdate(expected_version=value["version"],description=value["description"],lines=lines))
    assert changed["issues"]
    assert case.service.workbench(case.owner,filter="exception")["pagination"]["total"]==1
    assert case.service.get_book(case.owner,case.book,filter="exception",summaries=True)["pagination"]["total"]==1
