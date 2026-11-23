"""PLT05-C 受控子集补导：只补确认失败的那几笔，且绝不削弱旧守卫。

旧守卫（`bridge_outbox` 唯一约束 + `bridge_scope_guard` 全量清单校验）保持原样；
补导写独立的只追加表 `bridge_reissue_batches`。
"""

import pytest

from app.accounting.domain import AccountingError
from app.accounting.models import ExportRequest
from app.accounting.seed import LOCAL_ACTOR
from app.integrations.models import (
    BridgeConfiguration,
    BridgeExport,
    BridgeReissueCreate,
    BridgeReturnImport,
)
from app.integrations.service import FinancialBridgeService
from test_accounting import service, book, approve, import_rows, row  # noqa: F401
from test_tax08_tplus54 import configured

HEADER = "批次标识,凭证标识,对方凭证号,结果,失败原因,对方过账日期,金额,科目"


def _two_voucher_intent(service, book, bridge):
    """两张已审核凭证 → 一次导出 → 一份交付意图（旧守卫仍要求全量）。"""
    import_rows(
        service,
        book,
        [
            row(
                external_id=f"reissue-{index}:0001",
                amount=amount,
                evidence_ref=f"补导测试发票{index}",
            )
            for index, amount in enumerate(["0.30", "0.60"], start=1)
        ],
    )
    service.suggestions(LOCAL_ACTOR, book)
    drafts = service.get_book(LOCAL_ACTOR, book)["drafts"]
    assert len(drafts) == 2
    for draft in drafts:
        approve(service, book, draft)
    exported = service.export(
        LOCAL_ACTOR,
        book,
        ExportRequest(
            draft_ids=[draft["id"] for draft in drafts],
            idempotency_key="reissue-export-1",
        ),
    )
    intent = bridge.prepare_export(
        LOCAL_ACTOR,
        book,
        BridgeExport(
            export_id=exported["id"],
            configuration_version=1,
            reason="补导场景冻结交付",
            idempotency_key="reissue-intent-1",
        ),
    )
    return intent, drafts


def _return_file(batch_code, rows):
    lines = [HEADER] + [
        f"{batch_code},{code},TP-{index},{status},{reason},,,"
        for index, (code, status, reason, amount) in enumerate(rows, start=1)
    ]
    return chr(10).join(lines)


def _setup(service, book):
    bridge = configured(service, book)
    intent, drafts = _two_voucher_intent(service, book, bridge)
    artifact = bridge.create_artifact(LOCAL_ACTOR, book, intent["id"])
    # 返回文件里的凭证标识是适配层的 T54- 前缀形式（tplus54.build_rows 的约定）。
    codes = [f"T54-{item['external_code']}" for item in intent["package"]["vouchers"]]
    assert len(codes) == 2
    return bridge, intent, artifact, drafts, codes


def test_reissue_contains_only_confirmed_failed_vouchers(service, book):
    bridge, intent, artifact, _drafts, codes = _setup(service, book)
    bridge.record_return(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReturnImport(
            batch_code=intent["external_code"],
            content=_return_file(
                intent["external_code"],
                [
                    (codes[0], "accepted", "", "0.30"),
                    (codes[1], "rejected", "对方科目未启用", "0.60"),
                ],
            ),
        ),
    )
    result = bridge.create_reissue(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReissueCreate(idempotency_key="reissue-1", reason="只补对方拒绝的那一笔"),
    )
    rows = bridge.list_returns(LOCAL_ACTOR, book, artifact["id"])["returns"][0]["rows"]
    failed_codes = sorted(row["voucher_code"] for row in rows if row["status"] != "accepted")
    accepted_codes = sorted(row["voucher_code"] for row in rows if row["status"] == "accepted")
    assert failed_codes, "夹具必须至少有一笔确认失败"
    assert accepted_codes, "夹具必须至少有一笔已成功"
    assert result["failed_voucher_codes"] == failed_codes
    assert result["parent_outbox_id"] == intent["id"]
    assert result["parent_artifact_id"] == artifact["id"]
    assert result["voucher_count"] == len(failed_codes)
    assert result["excluded_voucher_count"] if "excluded_voucher_count" in result else True
    assert result["remapped"] is False
    # 补导文件只包含失败那一笔的凭证编码。
    download = bridge.download_reissue(LOCAL_ACTOR, book, result["id"])
    text = download["content"].decode("utf-8")
    for code in accepted_codes:
        assert code not in text, "补导文件不得包含已成功的凭证"
    for code in failed_codes:
        assert code in text
    # 幂等重放：同键返回同一条记录。
    replay = bridge.create_reissue(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReissueCreate(idempotency_key="reissue-1", reason="只补对方拒绝的那一笔"),
    )
    assert replay["id"] == result["id"] and replay["replayed"] is True
    # 换个键但内容相同：按内容指纹重放，不落第二份。
    same = bridge.create_reissue(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReissueCreate(idempotency_key="reissue-2", reason="重复点一次"),
    )
    assert same["id"] == result["id"] and same["replayed"] is True
    listed = bridge.list_reissues(LOCAL_ACTOR, book, artifact["id"])["reissues"]
    assert [item["id"] for item in listed] == [result["id"]]
    # 旧守卫仍在：原 intent 的整批文件与唯一约束没有被改动。
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM bridge_outbox WHERE export_id=?", (intent["export_id"],)
            ).fetchone()[0]
            == 1
        )


def test_reissue_refuses_while_the_result_is_still_unknown(service, book):
    bridge, _intent, artifact, _drafts, _codes = _setup(service, book)
    with pytest.raises(AccountingError) as refused:
        bridge.create_reissue(
            LOCAL_ACTOR,
            book,
            artifact["id"],
            BridgeReissueCreate(idempotency_key="reissue-unknown", reason="无回音就补导"),
        )
    assert refused.value.status == 409
    assert "result_unknown" in refused.value.detail or "尚无对方结果" in refused.value.detail


def test_reissue_refuses_when_nothing_failed(service, book):
    bridge, intent, artifact, _drafts, codes = _setup(service, book)
    bridge.record_return(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReturnImport(
            batch_code=intent["external_code"],
            content=_return_file(
                intent["external_code"],
                [(codes[0], "accepted", "", "0.30"), (codes[1], "accepted", "", "0.60")],
            ),
        ),
    )
    with pytest.raises(AccountingError) as refused:
        bridge.create_reissue(
            LOCAL_ACTOR,
            book,
            artifact["id"],
            BridgeReissueCreate(idempotency_key="reissue-none", reason="没有失败项也补导"),
        )
    assert refused.value.status == 409
    assert "没有确认失败" in refused.value.detail


def test_reissue_requires_write_permission_and_same_book(service, book):
    bridge, _intent, artifact, _drafts, _codes = _setup(service, book)
    other = service.create_book(
        LOCAL_ACTOR,
        __import__("app.accounting.models", fromlist=["BookCreate"]).BookCreate(
            name="另一个账套", period="2026-10"
        ),
    )["id"]
    bridge.record_return(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReturnImport(
            batch_code=bridge.get_outbox(LOCAL_ACTOR, book, _intent["id"])["external_code"],
            content=_return_file(
                bridge.get_outbox(LOCAL_ACTOR, book, _intent["id"])["external_code"],
                [("T54-missing-code", "rejected", "对方拒绝", "0.30")],
            ),
        ),
    )
    with pytest.raises(AccountingError):
        bridge.create_reissue(
            LOCAL_ACTOR,
            other,
            artifact["id"],
            BridgeReissueCreate(idempotency_key="reissue-cross", reason="跨账套补导"),
        )
    with service.db.transaction(actor=LOCAL_ACTOR) as conn:
        assert conn.execute("SELECT COUNT(*) FROM bridge_reissue_batches").fetchone()[0] == 0


def test_reissue_remaps_account_codes_when_mapping_changed(service, book):
    bridge, intent, artifact, _drafts, codes = _setup(service, book)
    bridge.record_return(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReturnImport(
            batch_code=intent["external_code"],
            content=_return_file(
                intent["external_code"],
                [(codes[0], "rejected", "对方科目未启用", "0.30")],
            )
            + chr(10)
            + f"{intent['external_code']},{codes[1]},TP-9,accepted,,,,",
        ),
    )
    bridge.configure(
        LOCAL_ACTOR,
        book,
        BridgeConfiguration(
            expected_version=1,
            external_book_id="tplus-demo",
            accounts=[
                {"local_code": "1002", "external_code": "100299"},
                {"local_code": "6602.02", "external_code": "660202"},
            ],
            reason="补导前重新核定科目映射",
            idempotency_key="reissue-config-v2",
        ),
    )
    result = bridge.create_reissue(
        LOCAL_ACTOR,
        book,
        artifact["id"],
        BridgeReissueCreate(idempotency_key="reissue-remap", reason="映射变更后补导失败项"),
    )
    assert result["remapped"] is True
    assert result["source_configuration_version"] == 1
    assert result["configuration_version"] == 2
    text = bridge.download_reissue(LOCAL_ACTOR, book, result["id"])["content"].decode("utf-8")
    assert "100299" in text
    assert "100201" not in text
