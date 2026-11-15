"""TAX08 合成 54 列适配文件与回执的真实 PostgreSQL 边界。"""

from __future__ import annotations

import csv
import io
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.accounting.service import Actor
from app.integrations.models import BridgeExport, BridgeReceiptImport
from app.integrations.service import FinancialBridgeService
from test_business_postgres import ADMIN_URL, APP_URL, business_case, business_postgres  # noqa: F401
from test_stage10_integrations_postgres import exported, prepare

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL,
    reason="需要专用 PostgreSQL 管理员及受限运行连接",
)


def artifact(case):
    service, _, _ = prepare(case)
    export = exported(case)
    intent = service.prepare_export(
        case.reviewer,
        case.book,
        BridgeExport(
            export_id=export["id"],
            configuration_version=1,
            reason="已核对合成 54 列交付",
            idempotency_key=uuid4().hex,
        ),
    )
    return service, intent, service.create_artifact(case.reviewer, case.book, intent["id"])


def receipt_text(content: bytes, status: str = "accepted") -> str:
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    codes = list(dict.fromkeys(row["外部凭证编号"] for row in rows))
    lines = ["行号,外部凭证编号,结果,原因"]
    lines += [f"{index},{code},{status}," for index, code in enumerate(codes, 1)]
    return "\n".join(lines) + "\n"


def test_pg_synthetic_54_artifact_receipt_and_rls(business_case):
    case = business_case
    service, intent, created = artifact(case)
    assert created["profile"] == "hengzhang.tplus.synthetic54.v1"
    assert created["synthetic"] is True and created["native_tplus_format"] is False
    assert created["voucher_count"] == 1

    filename, content = service.download_artifact(case.reviewer, case.book, created["id"])
    assert filename.endswith(".csv")
    assert content.startswith(b"\xef\xbb\xbf")
    assert created["row_count"] == len(content.splitlines()) - 1
    assert created["row_count"] == 1
    header = content.decode("utf-8-sig").splitlines()[0]
    assert len(header.split(",")) == 54

    body = BridgeReceiptImport(
        receipt_code="R-PG-54-1",
        content=receipt_text(content),
        reason="客户合成文件逐行回传",
    )
    recorded = service.record_receipt(case.reviewer, case.book, created["id"], body)
    assert recorded["result_status"] == "accepted"
    assert recorded["accepted_count"] == 1
    assert recorded["external_posting"] is False

    other_firm = "tax08-other-" + uuid4().hex[:10]
    outsider = Actor(user_id="tax08-outsider-" + uuid4().hex[:10], firm_id=other_firm)
    with case.psycopg.connect(ADMIN_URL) as conn:
        conn.execute(
            "INSERT INTO accounting.users(id,name) VALUES(%s,'其他事务所复核人')",
            (outsider.user_id,),
        )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,'隔离事务所',%s)",
            (other_firm, outsider.user_id),
        )
        conn.execute(
            "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,'reviewer')",
            (other_firm, outsider.user_id),
        )
    with case.db.transaction(actor=outsider) as conn:
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM bridge_export_artifacts WHERE id=?",
            (created["id"],),
        ).fetchone()["c"] == 0
        assert conn.execute(
            "SELECT COUNT(*) AS c FROM bridge_export_receipts WHERE artifact_id=?",
            (created["id"],),
        ).fetchone()["c"] == 0

    with pytest.raises(AccountingError):
        service.download_artifact(outsider, case.book, created["id"])

    with pytest.raises(AccountingError):
        service.dispatch(case.reviewer, case.book, intent["id"])
