"""DCP06-C：只为已有已复核报表的企业准备月度包草稿，发布仍由人确认。"""

from uuid import uuid4

import pytest

from app.batch_ops.service import BatchOperationsService
from test_business import business  # noqa: F401
from test_monthly_batch import monthly_batch  # noqa: F401


@pytest.fixture
def pack_case(monthly_batch):
    c = monthly_batch
    c.batches = BatchOperationsService(c.db, c.monthly_batch)
    return c


def test_preview_blocks_companies_without_a_reviewed_report(pack_case):
    c = pack_case
    plan = c.batches.preview_package_drafts(
        c.manager, [c.company["id"], c.other["id"], "missing"], "2026-10"
    )
    first = next(x for x in plan["companies"] if x["company_id"] == c.company["id"])
    assert first["blocked"] in {"no_report", "no_access"}
    assert plan["ready"] == 0
    assert len(plan["manifest"]) == 64


def test_create_requires_the_confirmed_manifest(pack_case):
    c = pack_case
    with pytest.raises(Exception) as error:
        c.batches.create_package_drafts(
            c.manager, [c.company["id"]], "2026-10", "f" * 64, uuid4().hex
        )
    assert getattr(error.value, "status", None) == 409
    plan = c.batches.preview_package_drafts(c.manager, [c.company["id"]], "2026-10")
    batch = c.batches.create_package_drafts(
        c.manager, [c.company["id"]], "2026-10", plan["manifest"], uuid4().hex
    )
    assert batch["kind"] == "package_draft"


def test_blocked_item_is_skipped_not_failed(pack_case):
    c = pack_case
    plan = c.batches.preview_package_drafts(c.manager, [c.company["id"]], "2026-10")
    c.batches.create_package_drafts(
        c.manager, [c.company["id"]], "2026-10", plan["manifest"], uuid4().hex
    )
    item = c.batches.claim("worker-c")
    result = c.batches.execute(item)
    assert result["status"] == "skipped"
    assert result["message"] in {"no_report", "no_book", "no_access"}
