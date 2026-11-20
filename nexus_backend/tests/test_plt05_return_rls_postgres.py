"""PLT05-C 补强：回执原件与逐笔处置在 PostgreSQL 上按账套（book）隔离。"""

import csv
import hashlib
import io
from uuid import uuid4

import pytest

from app.integrations.models import BridgeExport, BridgeReturnImport
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres  # noqa: F401
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


def _visible_returns(c, actor):
    with c.db.transaction(actor=actor) as conn:
        imports = conn.execute("SELECT COUNT(*) FROM bridge_return_imports").fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM bridge_return_rows").fetchone()[0]
    return imports, rows


def test_pg_return_imports_and_rows_are_book_scoped(business_case):
    c = business_case
    svc, intent, artifact = _artifact(c)
    _accepted_return(svc, c, intent, artifact)

    # 同所、同企业但没有该账套权限：父表与明细都不可见。
    assert _visible_returns(c, c.unassigned) == (0, 0)
    assert _visible_returns(c, c.reviewer) == (1, 1)

    # 同企业第二个账套 (B) 的授权人，不能读到账套 A 的回执明细。
    sibling_book = "rls-book-b-" + uuid4().hex
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.books(id,firm_id,company_id,name,period) VALUES(%s,%s,%s,'同企业另一账套','2026-10')",
            (sibling_book, c.owner.firm_id, c.company),
        )
    sibling = c.person("finance", c.department)
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,'editor')",
            (sibling_book, c.owner.firm_id, sibling.user_id),
        )
    assert _visible_returns(c, sibling) == (0, 0)

    # 普通运行角色不能把明细写进无权限账套的父回执。
    with pytest.raises(c.psycopg.errors.InsufficientPrivilege):
        with c.db.transaction(write=True, actor=c.unassigned) as conn:
            conn.execute(
                "INSERT INTO bridge_return_rows(import_id,voucher_code,status,raw_hash) "
                "VALUES(%s,'T54-OUTSIDER','accepted',%s)",
                (intent["external_code"] and _existing_import_id(c), uuid4().hex),
            )

    # 实时撤权后授权复核员也看不到。
    with c.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "DELETE FROM accounting.book_access WHERE book_id=%s AND user_id=%s",
            (c.book, c.reviewer.user_id),
        )
    assert _visible_returns(c, c.reviewer) == (0, 0)


def _existing_import_id(c):
    with c.db.transaction(actor=c.owner) as conn:
        return conn.execute("SELECT id FROM bridge_return_imports LIMIT 1").fetchone()[0]
