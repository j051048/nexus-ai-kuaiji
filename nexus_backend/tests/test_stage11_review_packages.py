import json
import sqlite3
from uuid import uuid4

import pytest
from app.accounting.domain import AccountingError
from app.accounting.package_models import PackagePrepare, PackageSelect
from app.accounting.packages import ReviewPackagesService
from app.accounting.review_models import ReviewConfirmRequest
from app.accounting.seed import LOCAL_ACTOR
from app.accounting.service import fingerprint, now
from app.ledgers.models import EntityCreate, EntityUpdate
from app.ledgers.service import LedgersService
from test_batch_review import case, draft, reviewer, status


def service(case):
    return ReviewPackagesService(case[0].db, case[1])


def prepare(case, items, actor=LOCAL_ACTOR, key=None):
    return service(case).prepare(actor, PackagePrepare(items=items, idempotency_key=key or uuid4().hex))


def stable_party(case, book):
    return LedgersService(case[0].db).create_entity(LOCAL_ACTOR, book, EntityCreate(kind="counterparty", code="SUP-1", name="测试供应商"))


def test_packages_group_confirmed_identity_and_keep_exact_differences(case):
    first = draft(case)
    second = draft(case, book=first["book_id"], external_id="second", description="办公用品另一笔采购")
    stable_party(case, first["book_id"])
    result = prepare(case, [first, second])
    assert len(result["packages"]) == 1 and result["ready_count"] == 2
    packet = result["packages"][0]
    assert packet["counterparty_id"] and packet["count"] == 2 and packet["total_amount"] == "200.00"
    assert {row["description"] for row in packet["items"]} == {"办公用品", "办公用品另一笔采购"}
    assert all(status(case, item)["status"] == "draft" for item in [first, second])
    assert packet["items"][0]["comparison"]["business_tax_amount"] is None


def test_unresolved_names_stay_individual_even_when_same_text(case):
    first = draft(case)
    second = draft(case, book=first["book_id"], external_id="second")
    result = prepare(case, [first, second])
    assert len(result["packages"]) == 2
    assert all(row["counterparty_id"] is None and row["count"] == 1 for row in result["packages"])


def test_choose_ready_group_avoids_blocked_same_book_and_keeps_export_explicit(case):
    first = draft(case)
    blocked = draft(case, book=first["book_id"], external_id="blocked", evidence="")
    parent = prepare(case, [first, blocked])
    assert parent["ready_count"] == parent["blocked_count"] == 1
    child = service(case).select(LOCAL_ACTOR, parent["id"], PackageSelect(draft_ids=[first["draft_id"]], idempotency_key=uuid4().hex))
    assert child["parent_preview_id"] == parent["id"] and child["ready_count"] == 1
    command = ReviewConfirmRequest(idempotency_key=uuid4().hex)
    receipt = service(case).confirm(LOCAL_ACTOR, child["id"], command)
    assert receipt["approved_count"] == 1
    assert service(case).confirm(LOCAL_ACTOR, child["id"], command) == receipt
    assert status(case, first)["status"] == "approved" and status(case, blocked)["status"] == "draft"
    with case[0].db.transaction() as conn:
        assert conn.execute("SELECT count(*) FROM exports").fetchone()[0] == 0


def test_package_replay_conflict_and_child_must_be_frozen_subset(case):
    first, second = draft(case), draft(case, external_id="second")
    key = uuid4().hex
    parent = prepare(case, [first], key=key)
    assert prepare(case, [first], key=key)["id"] == parent["id"]
    with pytest.raises(AccountingError, match="不能更改"):
        prepare(case, [second], key=key)
    with pytest.raises(AccountingError, match="不属于"):
        service(case).select(LOCAL_ACTOR, parent["id"], PackageSelect(draft_ids=[second["draft_id"]], idempotency_key=uuid4().hex))


def test_master_change_invalidates_package_and_selection(case):
    first = draft(case)
    party = stable_party(case, first["book_id"])
    parent = prepare(case, [first])
    LedgersService(case[0].db).update_entity(LOCAL_ACTOR, first["book_id"], party["id"], EntityUpdate(expected_version=1, name="已更正供应商", active=True, reason="会计核验供应商正式名称"))
    current = service(case).get(LOCAL_ACTOR, parent["id"])
    assert current["stale_count"] == 1 and current["ready_count"] == 0
    with pytest.raises(AccountingError, match="已变化"):
        service(case).select(LOCAL_ACTOR, parent["id"], PackageSelect(draft_ids=[first["draft_id"]], idempotency_key=uuid4().hex))


def test_revocation_hides_package_and_immutable_request_cannot_retarget(case):
    first = draft(case)
    actor = reviewer(case, [first["book_id"]])
    parent = prepare(case, [first], actor)
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        with case[0].db.transaction(write=True) as conn:
            conn.execute("UPDATE review_package_requests SET request_json='{}' WHERE id=?", (parent["id"],))
    with case[0].db.transaction(write=True) as conn:
        conn.execute("DELETE FROM book_access WHERE book_id=? AND user_id=?", (first["book_id"], actor.user_id))
    with pytest.raises(AccountingError):
        service(case).get(actor, parent["id"])


def test_forged_display_lines_next_to_real_fingerprint_cannot_approve(case):
    first = draft(case)
    original = prepare(case, [first])
    fake_id = uuid4().hex
    with case[0].db.transaction(write=True) as conn:
        preview = dict(conn.execute("SELECT * FROM review_previews WHERE id=?", (original["id"],)).fetchone())
        snapshots = json.loads(preview["snapshot_json"])
        snapshots[0]["lines"][0]["account_code"] = "6001"
        conn.execute("INSERT INTO review_previews(id,firm_id,created_by,request_fingerprint,snapshot_json,created_at,expires_at) VALUES(?,?,?,?,?,?,?)", (fake_id, LOCAL_ACTOR.firm_id, LOCAL_ACTOR.user_id, preview["request_fingerprint"], json.dumps(snapshots), now(), preview["expires_at"]))
        document = {"items": [first], "parent_preview_id": None}
        conn.execute("INSERT INTO review_package_requests VALUES(?,?,?,?,?,?,?,?)", (fake_id, LOCAL_ACTOR.firm_id, LOCAL_ACTOR.user_id, uuid4().hex, None, json.dumps(document), fingerprint(document), now()))
    assert service(case).get(LOCAL_ACTOR, fake_id)["stale_count"] == 1
    with pytest.raises(AccountingError, match="已变化"):
        service(case).confirm(LOCAL_ACTOR, fake_id, ReviewConfirmRequest(idempotency_key=uuid4().hex))
    assert status(case, first)["status"] == "draft"
