"""PLT05-C 补强：回执原件与逐笔处置在 PostgreSQL 上按账套（book）隔离。"""

import csv
import hashlib
import io
from uuid import uuid4

import pytest

from app.integrations.models import BridgeExport, BridgeReturnImport
from test_business_postgres import ADMIN_URL, APP_URL, business_case  # noqa: F401
from test_stage10_integrations_postgres import exported, prepare

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要专用 PostgreSQL 管理员和受限运行连接"
)


def _artifact(c):
    svc, _, _ = prepare(c)
    export = exported(c)
    intent = svc.prepare_export(
        c.reviewer,
        c.book,
        BridgeExport(
            export_id=export["id"],
            configuration_version=1,
            reason="合成回执账套隔离",
            idempotency_key=uuid4().hex,
        ),
    )
    return svc, intent, svc.create_artifact(c.reviewer, c.book, intent["id"])


def _accepted_return(svc, c, intent, artifact):
    voucher = intent["package"]["vouchers"][0]
    amount = sum(float(line["debit"] or 0) for line in voucher["lines"])
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(["批次标识", "凭证标识", "对方凭证号", "结果", "失败原因", "对方过账日期", "金额", "科目"])
    writer.writerow(
        [
            intent["external_code"],
            f"T54-{voucher['external_code']}",
            "T-RLS-1",
            "accepted",
            "",
            "2026-10-31",
            f"{amount:.2f}",
            "100201",
        ]
    )
    return svc.record_return(
        c.reviewer,
        c.book,
        artifact["id"],
        BridgeReturnImport(batch_code=intent["external_code"], content=buffer.getvalue()),
    )


def _seed_evidence_and_handler(c, result, voucher_code):
    content = b"%PDF-1.4 synthetic book-scope receipt"
    digest = hashlib.sha256(content).hexdigest()
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.bridge_return_evidence"
            "(import_id,book_id,firm_id,company_id,filename,media_type,sha256,size_bytes,"
            "content,created_by,created_at) VALUES(%s,%s,%s,%s,'客户回执.pdf',"
            "'application/pdf',%s,%s,%s,%s,now())",
            (
                result["id"],
                c.book,
                c.owner.firm_id,
                c.company,
                digest,
                len(content),
                content,
                c.reviewer.user_id,
            ),
        )
        conn.execute(
            "INSERT INTO accounting.bridge_return_row_handlers"
            "(import_id,voucher_code,book_id,firm_id,company_id,action,reason,"
            "evidence_ref,handled_by,handled_at,fingerprint) "
            "VALUES(%s,%s,%s,%s,%s,'resolved','客户确认差异并留痕','客户邮件归档',%s,now(),%s)",
            (
                result["id"],
                voucher_code,
                c.book,
                c.owner.firm_id,
                c.company,
                c.reviewer.user_id,
                uuid4().hex,
            ),
        )


def _visible_counts(c, actor):
    with c.db.transaction(actor=actor) as conn:
        evidence = conn.execute(
            "SELECT COUNT(*) FROM bridge_return_evidence"
        ).fetchone()[0]
        handlers = conn.execute(
            "SELECT COUNT(*) FROM bridge_return_row_handlers"
        ).fetchone()[0]
    return evidence, handlers


def test_pg_return_evidence_and_handlers_are_book_scoped(business_case):
    c = business_case
    svc, intent, artifact = _artifact(c)
    result = _accepted_return(svc, c, intent, artifact)
    voucher_code = f"T54-{intent['package']['vouchers'][0]['external_code']}"
    _seed_evidence_and_handler(c, result, voucher_code)

    # 同所、同企业但没有该账套权限的人：数据库层看不到回执内容。
    assert c.unassigned.firm_id == c.owner.firm_id
    assert _visible_counts(c, c.unassigned) == (0, 0)
    # 授权复核员仍可正常读取。
    assert _visible_counts(c, c.reviewer) == (1, 1)

    # 普通运行角色不能把处置记录写进无权限账套。
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.unassigned) as conn:
            conn.execute(
                "INSERT INTO bridge_return_row_handlers"
                "(import_id,voucher_code,book_id,firm_id,company_id,action,reason,"
                "evidence_ref,handled_by,handled_at,fingerprint) "
                "VALUES(%s,%s,%s,%s,%s,'resolved','越权写入测试','越权证据',%s,now(),%s)",
                (
                    result["id"],
                    voucher_code,
                    c.book,
                    c.owner.firm_id,
                    c.company,
                    c.unassigned.user_id,
                    uuid4().hex,
                ),
            )


def test_pg_return_evidence_follows_live_book_revocation(business_case):
    c = business_case
    svc, intent, artifact = _artifact(c)
    result = _accepted_return(svc, c, intent, artifact)
    _seed_evidence_and_handler(
        c, result, f"T54-{intent['package']['vouchers'][0]['external_code']}"
    )
    assert _visible_counts(c, c.reviewer) == (1, 1)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book, c.reviewer.user_id),
        )
    assert _visible_counts(c, c.reviewer) == (0, 0)
