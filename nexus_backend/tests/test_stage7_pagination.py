"""Bounded reads are authorized before paging; full guards run on detail/actions."""
import pytest
from fastapi.testclient import TestClient

from app.accounting.models import BookCreate, ImportRequest, DraftUpdate, ApprovalRequest, ExportRequest
from app.accounting.catalog import CatalogService
from app.accounting.catalog_models import AccountDefinition
from app.accounting.seed import LOCAL_ACTOR
from app.accounting.service import Actor, AccountingError
from app.main import create_app
from test_accounting import service, book, row, add_actor


def fill(service, book, count=45):
    service.import_sources(LOCAL_ACTOR,book,ImportRequest(source_name="分页合成数据",rows=[row(external_id=f"page-{i:03}") for i in range(count)]))
    service.suggestions(LOCAL_ACTOR,book)


def test_book_pages_bound_summaries_and_load_one_current_draft(service,book):
    fill(service,book)
    # A summary response must not execute expensive per-draft evidence guards.
    original=service._draft_json
    service._draft_json=lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError("eager detail"))
    first=service.get_book(LOCAL_ACTOR,book,limit=20,summaries=True)
    second=service.get_book(LOCAL_ACTOR,book,limit=20,offset=20,summaries=True)
    service._draft_json=original
    assert len(first["drafts"])==len(second["drafts"])==20
    assert first["pagination"]=={"limit":20,"offset":0,"total":45,"has_more":True}
    assert not {d["id"] for d in first["drafts"]}&{d["id"] for d in second["drafts"]}
    assert all(not d["lines"] and d["detail_loaded"] is False for d in first["drafts"])
    assert len(first["sources"])<=40 and len(first["source_page_ids"])==20
    actual=service.get_draft(LOCAL_ACTOR,book,first["drafts"][0]["id"])
    assert actual["detail_loaded"] and actual["lines"] and not actual["issues"]


def test_source_page_does_not_confuse_associated_sources_with_current_page(service,book):
    fill(service,book)
    first=service.get_book(LOCAL_ACTOR,book,limit=20,summaries=True)
    last=service.get_book(LOCAL_ACTOR,book,limit=20,source_offset=40,summaries=True)
    assert len(last["source_page_ids"])==5 and not last["source_pagination"]["has_more"]
    assert not set(first["source_page_ids"])&set(last["source_page_ids"])


def test_workbench_filters_and_counts_are_not_limited_to_first_page(service):
    books=[service.create_book(LOCAL_ACTOR,BookCreate(name=f"分页企业{i:02}",period="2026-10"))["id"] for i in range(23)]
    fill(service,books[-1],1)
    result=service.workbench(LOCAL_ACTOR,limit=20)
    assert len(result["books"])==20 and result["stats"]["book_count"]==23
    assert len(service.workbench(LOCAL_ACTOR,limit=20,offset=20)["books"])==3
    pending=service.workbench(LOCAL_ACTOR,limit=20,filter="pending")
    assert pending["pagination"]["total"]==1 and pending["books"][0]["id"]==books[-1]
    assert service.workbench(LOCAL_ACTOR,search="企业22")["books"][0]["id"]==books[-1]
    assert service.workbench(LOCAL_ACTOR,search="%") ["books"]==[]


def test_paging_never_leaks_unauthorized_totals_or_cross_book_detail(service,book):
    fill(service,book,2)
    hidden=service.create_book(LOCAL_ACTOR,BookCreate(name="不可见客户",period="2026-10"))["id"]
    fill(service,hidden,1)
    actor=add_actor(service, user_id="paged-reader", firm_id=LOCAL_ACTOR.firm_id, role="viewer",book_id=book)
    result=service.workbench(actor,limit=1,offset=1)
    assert result["books"]==[] and result["stats"]["book_count"]==1
    assert result["pagination"]["total"]==1
    hidden_draft=service.get_book(LOCAL_ACTOR,hidden)["drafts"][0]
    with pytest.raises(AccountingError): service.get_draft(actor,book,hidden_draft["id"])
    with pytest.raises(AccountingError): service.get_book(actor,hidden,summaries=True)


def test_evidence_navigation_can_focus_draft_beyond_first_page(service,book):
    fill(service,book)
    last=service.get_book(LOCAL_ACTOR,book,limit=20,offset=40,summaries=True)["drafts"][0]
    focused=service.get_book(LOCAL_ACTOR,book,limit=20,summaries=True,focus_draft_id=last["id"])
    assert focused["pagination"]["offset"]==40
    assert last["id"] in {d["id"] for d in focused["drafts"]}


@pytest.mark.parametrize("limit,offset",[(0,0),(101,0),(20,-1),(20,1000001),(True,0)])
def test_service_rejects_unbounded_page_parameters(service,limit,offset):
    with pytest.raises(AccountingError): service.workbench(LOCAL_ACTOR,limit=limit,offset=offset)


def test_http_pagination_and_single_draft_contract(tmp_path):
    with TestClient(create_app(tmp_path/"pagination-api.db",seed=False,queue_worker_enabled=False),base_url="http://127.0.0.1",client=("127.0.0.1",12345)) as client:
        client.headers["Authorization"]="Bearer "+client.get("/api/session").json()["token"]
        book=client.post("/api/books",json={"name":"API分页客户","period":"2026-10"}).json()["id"]
        fill(client.app.state.service,book,21)
        result=client.get(f"/api/books/{book}?summaries=true&limit=20").json()
        assert len(result["drafts"])==20 and result["pagination"]["total"]==21
        assert client.get(f"/api/books/{book}/drafts/{result['drafts'][0]['id']}").json()["detail_loaded"]
        assert client.get("/api/workbench?limit=1000").status_code==422


def test_missing_auxiliary_enters_exception_page_and_export_keeps_formula_safe_values(service,book):
    CatalogService(service.db).create_account(LOCAL_ACTOR,book,AccountDefinition(code="6602.97",name="按项目核算费用",category="expense",auxiliary_requirements=["project"]))
    fill(service,book,1)
    value=service.get_book(LOCAL_ACTOR,book)["drafts"][0]
    lines=[{**line,"account_code":"6602.97" if line["debit"]!="0.00" else line["account_code"]} for line in value["lines"]]
    changed=service.update_draft(LOCAL_ACTOR,book,value["id"],DraftUpdate(expected_version=value["version"],description=value["description"],lines=lines))
    assert changed["issues"]
    assert service.workbench(LOCAL_ACTOR,filter="exception")["stats"]["exception_count"]==1
    assert service.get_book(LOCAL_ACTOR,book,filter="exception",summaries=True)["pagination"]["total"]==1
    for line in lines:
        if line["account_code"]=="6602.97": line["auxiliary_values"]={"project":"=1+1"}
    changed=service.update_draft(LOCAL_ACTOR,book,value["id"],DraftUpdate(expected_version=changed["version"],description=value["description"],lines=lines))
    assert not changed["issues"]
    service.approve(LOCAL_ACTOR,book,value["id"],ApprovalRequest(expected_version=changed["version"]))
    receipt=service.export(LOCAL_ACTOR,book,ExportRequest(draft_ids=[value["id"]],idempotency_key="stage7-auxiliary-export"))
    import csv, io
    rows=list(csv.reader(io.StringIO(receipt["csv"].lstrip("\ufeff"))))
    assert rows[0][-4:]==["辅助部门","辅助人员","辅助交易对方","辅助项目"]
    cost=next(item for item in rows[1:] if item[3]=="6602.97")
    assert cost[11]=="'=1+1"


def test_company_choices_and_focus_are_independent_of_current_workbench_page(service):
    books=[service.create_book(LOCAL_ACTOR,BookCreate(name=f"账套{i:02}",period="2026-10")) for i in range(22)]
    target=books[-1]
    first=service.workbench(LOCAL_ACTOR,limit=20)
    assert target["id"] not in {item["id"] for item in first["books"]}
    choices=service.book_choices(LOCAL_ACTOR,target["company_id"],"2026-10")
    assert choices["books"][0]["id"]==target["id"]
    focused=service.workbench(LOCAL_ACTOR,limit=20,focus_book_id=target["id"])
    assert focused["pagination"]["offset"]==20 and target["id"] in {item["id"] for item in focused["books"]}
