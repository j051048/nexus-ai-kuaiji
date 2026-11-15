import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ExportRequest
from app.accounting.seed import LOCAL_ACTOR
from app.integrations import tplus54
from app.integrations.models import (
    BridgeConfiguration,
    BridgeExport,
    BridgeReceiptImport,
)
from app.integrations.service import FinancialBridgeService
from test_accounting import service, book, make_draft, approve, add_actor  # noqa: F401


def configured(service, book):
    bridge = FinancialBridgeService(service.db)
    bridge.configure(
        LOCAL_ACTOR,
        book,
        BridgeConfiguration(
            expected_version=0,
            external_book_id="tplus-demo",
            accounts=[
                {"local_code": "1002", "external_code": "100201"},
                {"local_code": "6602.02", "external_code": "660202"},
            ],
            reason="合成 54 列适配核验",
            idempotency_key="tplus54-config",
        ),
    )
    return bridge


def frozen_intent(service, book, bridge):
    draft = make_draft(service, book)
    approve(service, book, draft)
    exported = service.export(
        LOCAL_ACTOR,
        book,
        ExportRequest(draft_ids=[draft["id"]], idempotency_key="tplus54-export"),
    )
    return bridge.prepare_export(
        LOCAL_ACTOR,
        book,
        BridgeExport(
            export_id=exported["id"],
            configuration_version=1,
            reason="冻结交付",
            idempotency_key="tplus54-intent",
        ),
    )


def receipt(codes, status="accepted", reason=""):
    lines = ["行号,外部凭证编号,结果,原因"]
    lines += [
        f"{index},{code},{status},{reason}"
        for index, code in enumerate(codes, start=1)
    ]
    return "\n".join(lines) + "\n"


def test_synthetic_54_columns_are_frozen_downloadable_and_receipted(service, book):
    bridge = configured(service, book)
    intent = frozen_intent(service, book, bridge)
    artifact = bridge.create_artifact(LOCAL_ACTOR, book, intent["id"])
    assert artifact["profile"] == tplus54.PROFILE
    assert artifact["synthetic"] is True
    assert artifact["native_tplus_format"] is False
    assert artifact["row_count"] == 1
    assert artifact["voucher_count"] == 1
    assert bridge.create_artifact(LOCAL_ACTOR, book, intent["id"]) == artifact

    filename, content = bridge.download_artifact(LOCAL_ACTOR, book, artifact["id"])
    assert filename == artifact["filename"]
    assert content.startswith(b"\xef\xbb\xbf")
    lines = content.decode("utf-8-sig").splitlines()
    assert len(lines) == 2
    assert len(lines[0].split(",")) == 54
    assert len(lines[1].split(",")) == 54
    assert '"记"' in lines[1] or ",记," in lines[1]
    assert ",转," in lines[1]
    assert tplus54.content_hash(content) == artifact["content_sha256"]

    codes = tplus54.build_rows(intent["package"])[1]
    result = bridge.record_receipt(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReceiptImport(receipt_code="R-1", content=receipt(codes), reason="客户回传"),
    )
    assert result["result_status"] == "accepted"
    assert result["accepted_count"] == 1
    assert result["external_posting"] is False
    assert result["rows"][0]["voucher_external_code"] == codes[0]
    assert bridge.record_receipt(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReceiptImport(receipt_code="R-1", content=receipt(codes), reason="重复提交"),
    ) == result

    with pytest.raises(AccountingError, match="不同内容"):
        bridge.record_receipt(
            LOCAL_ACTOR,
            book,
            artifact["id"],
            BridgeReceiptImport(
                receipt_code="R-1",
                content=receipt(codes, status="rejected", reason="编号重复"),
                reason="同编号异内容",
            ),
        )


def test_artifact_rejects_changed_frozen_manifest_and_unreviewed_scope(service, book):
    bridge = configured(service, book)
    intent = frozen_intent(service, book, bridge)
    artifact = bridge.create_artifact(LOCAL_ACTOR, book, intent["id"])
    with service.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE bridge_export_artifacts SET content_sha256=? WHERE id=?",
            ("0" * 64, artifact["id"]),
        )
    with pytest.raises(AccountingError, match="旧适配文件"):
        bridge.download_artifact(LOCAL_ACTOR, book, artifact["id"])

    operator = add_actor(
        service,
        user_id="tplus54-operator",
        firm_id=LOCAL_ACTOR.firm_id,
        role="operator",
        book_id=book,
    )
    with pytest.raises(AccountingError):
        bridge.create_artifact(operator, book, intent["id"])


def test_receipt_parser_rejects_wrong_header_formula_and_missing_vouchers():
    with pytest.raises(AccountingError, match="四列模板"):
        tplus54.parse_result("a,b,c\n1,2,3\n".encode(), ["T54-1"])
    with pytest.raises(AccountingError, match="公式"):
        tplus54.parse_result(
            "行号,外部凭证编号,结果,原因\n1,T54-1,accepted,=cmd\n".encode(),
            ["T54-1"],
        )
    with pytest.raises(AccountingError, match="全部凭证"):
        tplus54.parse_result(
            "行号,外部凭证编号,结果,原因\n1,T54-other,accepted,\n".encode(),
            ["T54-1"],
        )
    with pytest.raises(AccountingError, match="原因"):
        tplus54.parse_result(
            "行号,外部凭证编号,结果,原因\n1,T54-1,rejected,\n".encode(),
            ["T54-1"],
        )


def test_formula_text_and_duplicate_voucher_codes_are_rejected():
    package = {
        "schema": "hengzhang.tplus.delivery.v1",
        "external_book_id": "tplus-demo",
        "period": "2026-10",
        "export_id": "export-1",
        "manifest_fingerprint": "manifest-1",
        "vouchers": [
            {
                "external_code": "=danger",
                "date": "2026-10-01",
                "description": "=SUM(A1:A2)",
                "lines": [
                    {"account_code": "1002", "account_name": "银行", "debit": "0.10", "credit": "0.00"},
                    {"account_code": "6602.02", "account_name": "费用", "debit": "0.00", "credit": "0.10"},
                ],
            }
        ],
    }
    rows, codes, _ = tplus54.build_rows(package)
    assert rows[0][0] == tplus54.PROFILE
    assert rows[0][5].startswith("T54-'=danger")
    assert rows[0][9].startswith("'=SUM")
    assert codes == ["T54-'=danger"]
    with pytest.raises(AccountingError, match="重复"):
        tplus54.build_rows(
            {
                **package,
                "vouchers": [
                    package["vouchers"][0],
                    {**package["vouchers"][0], "external_code": "=danger"},
                ],
            }
        )
    with pytest.raises(AccountingError, match="金额"):
        tplus54.build_rows(
            {
                **package,
                "vouchers": [
                    {
                        **package["vouchers"][0],
                        "lines": [
                            {"account_code": "1002", "debit": "0.001", "credit": "0.00"},
                            {"account_code": "6602.02", "debit": "0.00", "credit": "0.001"},
                        ],
                    }
                ],
            }
        )
