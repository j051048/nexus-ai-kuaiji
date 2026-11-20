"""Populated, synthetic A–D recovery evidence; never contacts an external system."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

from app.accounting.directory import DirectoryService
from app.accounting.models import BookCreate, BookMemberGrant, ExportRequest
from app.accounting.service import AccountingService
from app.integrations import tplus54
from app.integrations.models import BridgeConfiguration, BridgeExport, BridgeReceiptImport
from app.integrations.service import FinancialBridgeService
from app.ledgers.models import EntityCreate, TransportAliasCreate, TransportRelationCreate
from app.ledgers.service import LedgersService
from app.tax.fees import TaxFeeService
from app.tax.sources import TaxSourceAdapter
from test_tax06_fees import POLICY, ROWS, _masters, _source
from test_tax08_tplus54 import receipt


def seed_taxi_recovery(db, owner, reviewer, company_id):
    accounting = AccountingService(db)
    book = accounting.create_book(
        owner, BookCreate(name="虚构出租车有数据恢复验收", period="2026-10", company_id=company_id)
    )
    DirectoryService(db).grant_book_member(
        owner, book["id"], reviewer.user_id, BookMemberGrant(role="reviewer")
    )
    case = SimpleNamespace(db=db, manager=owner, book=book)
    ledger = LedgersService(db)
    subjects = _masters(case, with_mapping=False)
    auxiliaries = []
    for index, subject in enumerate(subjects):
        entity = ledger.create_entity(
            owner,
            book["id"],
            EntityCreate(
                kind="counterparty" if subject["kind"] == "driver" else "project",
                code=f"RECOVERY-{index}",
                name=f"虚构恢复核算对象{index}",
            ),
        )
        auxiliaries.append(
            {"kind": entity["kind"], "local_value": entity["name"], "external_code": entity["code"]}
        )
        ledger.create_transport_relation(
            owner,
            book["id"],
            TransportRelationCreate(
                subject_id=subject["id"],
                target_id=entity["id"],
                contract_external_id="C1",
                operating_right="虚构本地验收",
                valid_from="2026-10-01",
                valid_to="2026-10-31",
            ),
        )
        ledger.add_transport_alias(
            owner,
            book["id"],
            TransportAliasCreate(
                subject_id=subject["id"], alias=f"恢复别名{index}", source="离线夹具"
            ),
        )
    source = _source(case, rows=ROWS[:3])
    fees = TaxFeeService(db)
    batch = fees.generate(
        owner, book["id"], period=book["period"], source_file_ids=[source["id"]], policy=POLICY
    )
    binding = TaxSourceAdapter(db).bind_output(
        owner,
        book["id"],
        source["id"],
        output_kind="approval",
        output_id=batch["draft_id"],
        output_version=1,
    )
    reviewed = fees.review(
        reviewer, book["id"], batch["id"], expected_version=1, reason="有数据恢复独立复核"
    )
    assert reviewed["status"] == "reviewed"
    exported = accounting.export(
        owner,
        book["id"],
        ExportRequest(
            draft_ids=[batch["draft_id"]], idempotency_key="taxi-recovery-" + uuid4().hex
        ),
    )
    bridge = FinancialBridgeService(db)
    bridge.configure(
        owner,
        book["id"],
        BridgeConfiguration(
            expected_version=0,
            external_book_id="synthetic-only-recovery",
            accounts=[{"local_code": code, "external_code": code} for code in ("1122", "6001")],
            auxiliaries=auxiliaries,
            reason="合成恢复映射，不代表客户T+适配",
            idempotency_key="taxi-recovery-config",
        ),
    )
    intent = bridge.prepare_export(
        owner,
        book["id"],
        BridgeExport(
            export_id=exported["id"],
            configuration_version=1,
            reason="恢复冻结集合",
            idempotency_key="taxi-recovery-intent",
        ),
    )
    artifact = bridge.create_artifact(owner, book["id"], intent["id"])
    result = bridge.record_receipt(
        owner,
        book["id"],
        artifact["id"],
        BridgeReceiptImport(
            receipt_code="RECOVERY-UNKNOWN",
            content=receipt(
                tplus54.build_rows(intent["package"])[1],
                status="unknown",
                reason="离线结果待人工核查",
            ),
            reason="只记录合成未知结果，未外发",
        ),
    )
    assert result["result_status"] == "unknown" and result["external_posting"] is False
    return {
        "company_id": company_id,
        "book_id": book["id"],
        "source": source,
        "batch": reviewed,
        "binding": binding,
        "artifact": artifact,
        "receipt": result,
        "intent_id": intent["id"],
        "draft_id": batch["draft_id"],
    }


def taxi_snapshot(conn, evidence, *, postgres=False):
    """Require populated records and capture full rows, not table existence."""
    prefix, placeholder = ("accounting.", "%s") if postgres else ("", "?")
    book_id, company_id = evidence["book_id"], evidence["company_id"]
    queries = {
        **{
            name: ("company_id", company_id)
            for name in (
                "transport_subjects",
                "transport_relations",
                "transport_assignments",
                "transport_aliases",
            )
        },
        **{
            name: ("book_id", book_id)
            for name in (
                "tax_source_files",
                "tax_source_bindings",
                "tax_fee_batches",
                "draft_line_sources",
                "bridge_export_artifacts",
                "bridge_export_receipts",
            )
        },
        "tax_fee_batch_rows": ("batch_id", evidence["batch"]["id"]),
        "bridge_export_receipt_rows": ("receipt_id", evidence["receipt"]["id"]),
        "draft_review_receipts": ("draft_id", evidence["draft_id"]),
    }
    captured = {}
    for table, (column, value) in queries.items():
        cursor = conn.execute(
            f"SELECT * FROM {prefix}{table} WHERE {column}={placeholder}", (value,)
        )
        rows = cursor.fetchall()
        assert rows, f"出租车恢复验收缺少实际数据：{table}"
        keys = [item[0] for item in cursor.description]
        values = [dict(zip(keys, row)) if not hasattr(row, "keys") else dict(row) for row in rows]
        captured[table] = sorted(
            json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) for row in values
        )
    return captured


def verify_taxi_recovery_services(db, actor, evidence):
    source, batch, artifact = evidence["source"], evidence["batch"], evidence["artifact"]
    filename, original = TaxSourceAdapter(db).download_original(
        actor, evidence["book_id"], source["id"]
    )
    assert filename == source["filename"]
    assert hashlib.sha256(original).hexdigest() == source["sha256"]
    restored = TaxFeeService(db).get(actor, evidence["book_id"], batch["id"])
    assert (
        restored["status"],
        restored["version"],
        restored["total_cents"],
        restored["stale"],
    ) == (
        "reviewed",
        2,
        17500,
        False,
    )
    bridge = FinancialBridgeService(db)
    _, content = bridge.download_artifact(actor, evidence["book_id"], artifact["id"])
    assert hashlib.sha256(content).hexdigest() == artifact["content_sha256"]
    receipts = bridge.list_receipts(actor, evidence["book_id"], artifact["id"])["receipts"]
    assert len(receipts) == 1
    assert receipts[0]["result_status"] == "unknown" and receipts[0]["external_posting"] is False


def verify_taxi_recovery_http(client, headers, evidence):
    book, source = evidence["book_id"], evidence["source"]
    original_path = f"/api/tax/books/{book}/sources/{source['id']}/original"
    response = client.get(original_path, headers=headers["owner"])
    assert response.status_code == 200
    assert hashlib.sha256(response.content).hexdigest() == source["sha256"]
    fee_path = f"/api/tax/books/{book}/fee-batches/{evidence['batch']['id']}"
    result = client.get(fee_path, headers=headers["reviewer"])
    assert result.status_code == 200
    assert result.json()["status"] == "reviewed" and result.json()["stale"] is False
    base = f"/api/books/{book}/integrations/tplus/artifacts/{evidence['artifact']['id']}"
    result = client.get(base + "/download", headers=headers["owner"])
    assert result.status_code == 200
    assert hashlib.sha256(result.content).hexdigest() == evidence["artifact"]["content_sha256"]
    result = client.get(base + "/receipts", headers=headers["owner"])
    assert result.status_code == 200 and len(result.json()["receipts"]) == 1
    assert result.json()["receipts"][0]["result_status"] == "unknown"
    assert result.json()["receipts"][0]["external_posting"] is False
    for path in (original_path, fee_path, base + "/download", base + "/receipts"):
        assert client.get(path, headers=headers["outsider"]).status_code in {403, 404}
        assert client.get(path, headers=headers["employee"]).status_code in {403, 404}
    return {
        "taxi_original": True,
        "taxi_review": True,
        "taxi_unknown_preserved": True,
        "taxi_tenant_permissions": True,
    }
