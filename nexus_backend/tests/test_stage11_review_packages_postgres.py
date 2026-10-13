import json
from uuid import uuid4

import pytest
from app.accounting.domain import AccountingError
from app.accounting.package_models import PackagePrepare, PackageSelect
from app.accounting.packages import ReviewPackagesService
from app.accounting.review import ReviewService
from app.accounting.review_models import ReviewConfirmRequest, ReviewPreviewRequest
from app.accounting.service import fingerprint, now
from app.ledgers.models import EntityCreate
from app.ledgers.service import LedgersService
from test_batch_review_postgres import bank_draft
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres

pytestmark = pytest.mark.skipif(not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员及受限运行测试连接")


def prepare(c, items, actor=None, key=None):
    service = ReviewPackagesService(c.db, ReviewService(c.db, c.service))
    result = service.prepare(actor or c.owner, PackagePrepare(items=items, idempotency_key=key or uuid4().hex))
    return service, result


def test_pg_package_scope_full_checks_selection_and_idempotent_human_confirmation(business_case):
    c = business_case
    first, second = bank_draft(c, c.book, uuid4().hex), bank_draft(c, c.book, uuid4().hex)
    LedgersService(c.db).create_entity(c.owner, c.book, EntityCreate(kind="counterparty", code="SUP", name="供应商"))
    key = uuid4().hex
    service, parent = prepare(c, [first, second], key=key)
    assert len(parent["packages"]) == 1 and parent["ready_count"] == 2
    assert prepare(c, [first, second], key=key)[1]["id"] == parent["id"]
    child = service.select(c.owner, parent["id"], PackageSelect(draft_ids=[first["draft_id"]], idempotency_key=uuid4().hex))
    command = ReviewConfirmRequest(idempotency_key=uuid4().hex)
    receipt = service.confirm(c.owner, child["id"], command)
    assert receipt["approved_count"] == 1 and service.confirm(c.owner, child["id"], command) == receipt
    with c.db.transaction(actor=c.owner) as conn:
        assert conn.execute("SELECT status FROM drafts WHERE id=?", (second["draft_id"],)).fetchone()[0] == "draft"
        assert conn.execute("SELECT count(*) FROM exports WHERE book_id=?", (c.book,)).fetchone()[0] == 0


def test_pg_package_force_rls_private_owner_and_revocation(business_case):
    c = business_case
    item = bank_draft(c, c.book, uuid4().hex)
    service, parent = prepare(c, [item], c.reviewer)
    with c.psycopg.connect(ADMIN_URL) as conn:
        assert conn.execute("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='accounting.review_package_requests'::regclass").fetchone() == (True, True)
        assert not conn.execute("SELECT has_table_privilege('accounting_worker','accounting.review_package_requests','SELECT')").fetchone()[0]
    for actor in (c.employee, c.viewer, c.owner):
        with c.db.transaction(actor=actor) as conn:
            assert conn.execute("SELECT * FROM review_package_requests WHERE id=?", (parent["id"],)).fetchall() == []
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute("DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s", (c.book, c.reviewer.user_id))
    with pytest.raises(AccountingError):
        service.get(c.reviewer, parent["id"])


def test_pg_native_package_parent_retarget_and_forged_request_hash_rejected(business_case):
    c = business_case
    first, other = bank_draft(c, c.book, uuid4().hex), bank_draft(c, c.book, uuid4().hex)
    _, parent = prepare(c, [first])
    review = ReviewService(c.db, c.service)
    outsider = review.preview(c.owner, ReviewPreviewRequest(items=[other]))
    document = {"items": [other], "parent_preview_id": parent["id"]}
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # The raw child preview has a different request hash; both it and the parent
    # must exactly bind the selection, even for a currently authorized owner.
    with pytest.raises(c.psycopg.errors.CheckViolation):
        with c.db.transaction(write=True, actor=c.owner) as conn:
            conn.execute("INSERT INTO review_package_requests VALUES(?,?,?,?,?,?,?,?)", (outsider["id"], c.owner.firm_id, c.owner.user_id, uuid4().hex, parent["id"], text, fingerprint(document), now()))


def test_pg_fake_display_cannot_be_confirmed_with_copied_real_fingerprint(business_case):
    c = business_case
    item = bank_draft(c, c.book, uuid4().hex)
    service, real = prepare(c, [item])
    fake_id = uuid4().hex
    with c.db.transaction(write=True, actor=c.owner) as conn:
        preview = dict(conn.execute("SELECT * FROM review_previews WHERE id=?", (real["id"],)).fetchone())
        snapshots = json.loads(preview["snapshot_json"])
        snapshots[0]["lines"][0]["account_code"] = "6001"
        request = dict(conn.execute("SELECT * FROM review_package_requests WHERE id=?", (real["id"],)).fetchone())
        conn.execute("INSERT INTO review_previews VALUES(?,?,?,?,?,?,?)", (fake_id, c.owner.firm_id, c.owner.user_id, preview["request_fingerprint"], json.dumps(snapshots), now(), preview["expires_at"]))
        conn.execute("INSERT INTO review_package_requests VALUES(?,?,?,?,?,?,?,?)", (fake_id, c.owner.firm_id, c.owner.user_id, uuid4().hex, None, request["request_json"], request["request_fingerprint"], now()))
    assert service.get(c.owner, fake_id)["stale_count"] == 1
    with pytest.raises(AccountingError, match="已变化"):
        service.confirm(c.owner, fake_id, ReviewConfirmRequest(idempotency_key=uuid4().hex))
