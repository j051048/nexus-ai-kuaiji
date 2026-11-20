"""P0-3 / PLT06: encrypted backup -> isolated restore -> controlled hold -> real business continuation.

Verifies that after restoring an encrypted backup into an isolated database and originals directory:
1. Restored state starts held (`serving_enabled=False`, `outbound_disabled=True`, `restore_state.hold=1`).
2. While held, outbound notification dispatch and queueing fail closed (409).
3. After explicit hold release (with audit trail), real business continues cleanly on the restored DB:
   - original submitter opens the original uploaded attachment with identical bytes & SHA-256;
   - independent reviewer approves a pending draft and posts it into a formal voucher (self-approval still 403);
   - a revoked former member cannot read the restored book or attachment (403/404);
   - an in-flight `unknown` outbound notification is never re-sent or silently re-queued (409).
"""

from __future__ import annotations

import base64
import hashlib
import sqlite3
from types import SimpleNamespace

import pytest

from app.accounting.database import Database
from app.accounting.domain import Actor, AccountingError
from app.accounting.models import (
    ApprovalRequest,
    BookCreate,
    BookMemberGrant,
    CompanyMemberGrant,
    FirmMemberGrant,
    ImportRequest,
    SourceRow,
)
from app.accounting.service import AccountingService
from app.business.models import BusinessEventCreate, BusinessPayload
from app.business.service import BusinessService
from app.business.storage import AttachmentStore
from app.delivery.followup_models import DeliveryTaskRequest
from app.delivery.followups import DeliveryFollowupService, LocalSinkChannel
from app.posting.models import OpeningBind, PostDraft
from app.posting.service import PostingService
from scripts import backup_accounting as recovery
from test_delivery_followups import (  # noqa: F401 - pytest fixture chain
    business,
    case,
    close_case,
    freeze,
    grant,
    queue,
    ready_case,
)
from test_posting_service import frozen_opening


def _aes256_key(byte: int) -> str:
    return base64.b64encode(bytes([byte]) * 32).decode("ascii")


def _sample_pdf() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<<>>endobj\n"
        b"2 0 obj<< /Length 36 >>stream\n"
        b"PLT06 synthetic recovery original\n"
        b"endstream endobj\n"
        b"%%EOF\n"
    )


def test_encrypted_restore_and_real_business_continuation(case, tmp_path, monkeypatch):
    monkeypatch.setenv("ACCOUNTING_BACKUP_KEY", _aes256_key(11))
    monkeypatch.setenv("ACCOUNTING_BACKUP_KEY_ID", "plt06-drill-key")
    monkeypatch.setenv("ACCOUNTING_ENV", "test")

    # 1) 停机前准备业务现场：
    #    - 2026-10 已锁期账套：冻结月度交付包 + 一条外发后结果未知（unknown）的通知
    class LostReceipt(LocalSinkChannel):
        def deliver(self, payload):
            raise TimeoutError("synthetic provider timeout after send")

    freeze(case)
    grant(case)
    queue(case)
    case.followups.dispatch(LostReceipt())

    #    - 同一家企业 2026-11 未锁期账套：含真实原件的业务单 + 待独立复核人审核过账的凭证草稿
    storage = AttachmentStore(case.sub.storage.root)
    biz = BusinessService(case.db)
    pdf_bytes = _sample_pdf()
    pdf_sha = hashlib.sha256(pdf_bytes).hexdigest()
    event = biz.create(
        case.actors["employee"],
        BusinessEventCreate(
            company_id=case.company["id"],
            template_id="reimbursement",
            payload=BusinessPayload(
                occurred_on="2026-11-05",
                description="停机前上传的合成出租车规费票据",
                counterparty="合成城市交管代收点",
                amount="350.00",
                direction="expense",
                payment_status="unpaid",
                category="office",
            ),
        ),
    )
    stored_meta = storage.put_original("停机前票据原件.pdf", "application/pdf", pdf_bytes)
    attached = biz.add_attachment(
        case.actors["employee"],
        event["id"],
        event["version"],
        stored_meta,
    )
    attachment_id = attached["attachments"][0]["id"]

    next_book = case.other_book
    case.directory.grant_book_member(
        case.manager, next_book["id"], case.reviewer.user_id, BookMemberGrant(role="reviewer")
    )
    sub_case = SimpleNamespace(**{**vars(case), "company": case.other, "book": next_book})
    opening = frozen_opening(sub_case)
    PostingService(case.db).bind_opening(
        case.manager,
        OpeningBind(
            book_id=next_book["id"],
            package_version_id=opening["version_id"],
            reason="PLT06 停机前绑定期初",
        ),
    )
    case.ledger.import_sources(
        case.manager,
        next_book["id"],
        ImportRequest(
            source_name="停机前规费银行流水",
            rows=[
                SourceRow(
                    external_id="plt06-src-1",
                    date="2026-10-15",
                    description="办公用品报销支出",
                    counterparty="合成城市办公商店",
                    amount="350.00",
                    direction="expense",
                    evidence_ref="合成回单 PLT06-001",
                )
            ],
        ),
    )
    case.ledger.suggestions(case.manager, next_book["id"])
    book_state = case.ledger.get_book(case.manager, next_book["id"])
    draft = book_state["drafts"][0]
    draft_id = draft["id"]
    draft_version = draft["version"]

    #    - 停机前撤权一名前成员，确保撤权状态完整随备份恢复
    former_identity = SimpleNamespace(
        user_id="former-staff-plt06",
        display_name="停机前离职成员",
        email="former-plt06@example.test",
    )
    case.directory.register_identity(former_identity)
    case.directory.grant_firm_member(
        case.manager, FirmMemberGrant(email=former_identity.email, role="viewer")
    )
    case.directory.grant_company_member(
        case.manager,
        case.company["id"],
        CompanyMemberGrant(
            email=former_identity.email,
            role="employee",
            department_id=case.department["id"],
        ),
    )
    case.directory.grant_book_member(
        case.manager, next_book["id"], former_identity.user_id, BookMemberGrant(role="viewer")
    )
    case.directory.revoke_book_member(case.manager, next_book["id"], former_identity.user_id)
    case.directory.revoke_firm_member(case.manager, former_identity.user_id)
    case.directory.revoke_company_member(
        case.manager, case.company["id"], former_identity.user_id
    )
    former_actor = Actor(former_identity.user_id, case.manager.firm_id)

    # 2) 加密备份 + 校验密文 + 恢复到全新隔离目录
    archive = tmp_path / "encrypted-backup"
    restored = tmp_path / "isolated-restore"
    manifest = recovery.backup(case.db.path, case.sub.storage.root, archive)
    assert manifest["encryption"]["algorithm"] == "AES-256-GCM"
    assert manifest["contains_credentials"] is False
    assert (archive / "database.sqlite").read_bytes().startswith(b"NXBK1")
    assert recovery.verify(archive) == manifest

    receipt = recovery.restore(archive, restored)
    assert receipt["serving_enabled"] is False
    assert receipt["outbound_disabled"] is True
    assert receipt["worker_autostart"] is False
    assert receipt["verified_files"] >= 3

    copied_db = Database(restored / "database.sqlite")
    restored_storage = AttachmentStore(restored / "originals")

    # 3) 受控保持期内：数据库 hold=1 阻断外发调度与排单
    with copied_db.transaction() as conn:
        hold_row = conn.execute(
            "SELECT hold,restore_id FROM restore_state WHERE instance_id='restored'"
        ).fetchone()
    assert hold_row is not None and hold_row["hold"] == 1
    restored_followups = DeliveryFollowupService(copied_db)
    with pytest.raises(AccountingError) as held_err:
        restored_followups.dispatch(LocalSinkChannel())
    assert held_err.value.status == 409

    # 4) 人工核验完毕后解除保持并记录审计说明
    with sqlite3.connect(copied_db.path) as conn:
        conn.execute(
            "UPDATE restore_state SET hold=0,reason='PLT06 隔离库核验通过解除保持',"
            "updated_by='ops-lead-plt06' WHERE instance_id='restored'"
        )
    (restored / "restore-verification.json").rename(restored / "restore-released.json")

    # 5) 解除保持后在隔离库上真实继续办单：
    #    5a) 原经办人打开原件附件，字节与 SHA-256 完全一致
    restored_biz = BusinessService(copied_db)
    meta = restored_biz.download_metadata(case.actors["employee"], attachment_id)
    restored_bytes = restored_storage.read(meta)
    assert restored_bytes == pdf_bytes
    assert hashlib.sha256(restored_bytes).hexdigest() == pdf_sha

    #    5b) 已撤权成员访问原件和账套均被严格拒绝（403/404）
    with pytest.raises(AccountingError) as revoked_att:
        restored_biz.download_metadata(former_actor, attachment_id)
    assert revoked_att.value.status in (403, 404)

    restored_accounting = AccountingService(copied_db)
    with pytest.raises(AccountingError) as revoked_book:
        restored_accounting.get_book(former_actor, next_book["id"])
    assert revoked_book.value.status in (403, 404)

    #    5c) 独立复核铁律依旧生效：原制单人自审被 403 拒绝，独立复核人审核并成功过账
    with pytest.raises(AccountingError) as self_approve:
        restored_accounting.approve(
            case.manager,
            next_book["id"],
            draft_id,
            ApprovalRequest(expected_version=draft_version),
        )
    assert self_approve.value.status == 403

    approved_draft = restored_accounting.approve(
        case.reviewer,
        next_book["id"],
        draft_id,
        ApprovalRequest(expected_version=draft_version),
    )
    assert approved_draft["status"] == "approved"

    posted = PostingService(copied_db).post_draft(
        case.reviewer,
        next_book["id"],
        draft_id,
        PostDraft(
            command_key="plt06-post-after-restore-" + draft_id,
            expected_version=approved_draft["version"],
            voucher_date="2026-10-15",
        ),
    )
    voucher = posted.get("voucher", posted)
    assert voucher["voucher_no"]
    assert voucher["status"] == "posted"

    #    5d) 停机前状态 unknown 的通知永不自动重发，也不得靠新恢复纪元偷偷重排
    dispatch_res = restored_followups.dispatch(LocalSinkChannel())
    assert dispatch_res["sent"] == 0
    with copied_db.transaction() as conn:
        notif = conn.execute("SELECT state,attempts FROM delivery_notifications").fetchone()
    assert notif["state"] == "unknown" and notif["attempts"] == 1
    with pytest.raises(AccountingError) as requeue_err:
        restored_followups.queue(
            case.reviewer,
            case.book["id"],
            DeliveryTaskRequest(
                period="2026-10",
                kind="signature_reminder",
                reason="恢复后不得重排未查明结果的旧通知",
            ),
        )
    assert requeue_err.value.status == 409
