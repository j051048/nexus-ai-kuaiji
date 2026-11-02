"""DCP06-B：批量受控导出只导出已复核且版本未变的凭证，逐项留导出引用。"""

from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.batch_ops.service import BatchOperationsService
from c01_bank_fixture import reviewed_export_draft
from test_business import business  # noqa: F401
from test_monthly_batch import monthly_batch  # noqa: F401
from test_posting_service import admin


@pytest.fixture
def export_case(monthly_batch):
    c = monthly_batch
    c.reviewer = admin(c)
    c.batches = BatchOperationsService(c.db, c.monthly_batch)
    return c


def test_preview_lists_approved_drafts_and_reports_blockers(export_case):
    c = export_case
    draft = reviewed_export_draft(c)
    plan = c.batches.preview_review_export(
        c.manager, [c.company["id"], c.other["id"]], "2026-10"
    )
    ready = next(x for x in plan["companies"] if x["company_id"] == c.company["id"])
    assert ready["drafts"] == [{"draft_id": draft["id"], "version": draft["version"]}]
    assert len(ready["manifest"]) == 64
    blocked = next(x for x in plan["companies"] if x["company_id"] == c.other["id"])
    assert blocked["blocked"] in {"no_approved", "no_access", "no_book"}
    assert plan["ready"] == 1


def test_manifest_changes_when_another_approved_draft_appears(export_case):
    c = export_case
    reviewed_export_draft(c)
    first = c.batches.preview_review_export(c.manager, [c.company["id"]], "2026-10")
    second_draft = reviewed_export_draft(c)
    second = c.batches.preview_review_export(c.manager, [c.company["id"]], "2026-10")
    assert first["manifest"] != second["manifest"]
    assert second_draft["id"] in {
        item["draft_id"] for item in second["companies"][0]["drafts"]
    }


def test_create_requires_the_confirmed_manifest(export_case):
    c = export_case
    reviewed_export_draft(c)
    plan = c.batches.preview_review_export(c.manager, [c.company["id"]], "2026-10")
    with pytest.raises(AccountingError) as error:
        c.batches.create_review_export(
            c.manager, [c.company["id"]], "2026-10", "0" * 64, uuid4().hex
        )
    assert error.value.status == 409
    batch = c.batches.create_review_export(
        c.manager, [c.company["id"]], "2026-10", plan["manifest"], uuid4().hex
    )
    assert batch["kind"] == "review_export"
    assert batch["total"] == 1


def test_execute_exports_the_pinned_drafts_and_records_reference(export_case):
    c = export_case
    draft = reviewed_export_draft(c)
    plan = c.batches.preview_review_export(c.manager, [c.company["id"]], "2026-10")
    batch = c.batches.create_review_export(
        c.manager, [c.company["id"]], "2026-10", plan["manifest"], uuid4().hex
    )
    item = c.batches.claim("worker-1") or c.batches.claim("worker-1")
    assert item["kind"] == "review_export"
    result = c.batches.execute(item)
    assert result["status"] == "created"
    assert result["export_id"]
    assert result["count"] == 1
    assert result["filename"].endswith(".csv")
    with c.db.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM exports WHERE id=?", (result["export_id"],)
        ).fetchone()
        assert row["book_id"] == c.book["id"]
        assert row["request_fingerprint"]
    c.batches.finish(item, "succeeded", result=result)
    saved = c.batches.get(c.manager, batch["id"])
    assert saved["items"][0]["status"] == "succeeded"
    assert saved["items"][0]["result"]["export_id"] == result["export_id"]
    assert draft["id"] in json_ids(c, batch["id"])


def json_ids(case, batch_id):
    import json as _json

    with case.db.transaction() as conn:
        row = conn.execute(
            "SELECT request_json FROM batch_operations WHERE id=?", (batch_id,)
        ).fetchone()
    request = _json.loads(row["request_json"])
    return {draft["draft_id"] for company in request["companies"] for draft in company["drafts"]}


def test_execute_refuses_after_the_source_changes(export_case):
    c = export_case
    reviewed_export_draft(c)
    plan = c.batches.preview_review_export(c.manager, [c.company["id"]], "2026-10")
    c.batches.create_review_export(
        c.manager, [c.company["id"]], "2026-10", plan["manifest"], uuid4().hex
    )
    item = c.batches.claim("worker-1") or c.batches.claim("worker-1")
    # 预览后又批准了一张凭证：集合变了，必须拒绝而不是按旧清单导出。
    reviewed_export_draft(c)
    with pytest.raises(AccountingError) as error:
        c.batches.execute(item)
    assert error.value.status == 409
    assert "重新预览" in error.value.detail
