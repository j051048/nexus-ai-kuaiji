"""Explicit local-drill release; never a production switch or automatic replay.

An administrator must supply completed independent verification and the exact
live inventory digest. Unknown charges/receipts remain unchanged and quarantined
by the normal attempt fencing. No worker or external provider is started here.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

try:
    from scripts.recovery_security import (
        capture_security,
        digest,
        verify_security_owners,
        verify_sequences,
    )
except ModuleNotFoundError:
    from recovery_security import capture_security, digest, verify_security_owners, verify_sequences

REQUIRED_CHECKS = {
    "database",
    "originals",
    "identity",
    "permissions",
    "workflow",
    "outbound_reconciled",
}
try:
    from scripts.recovery_review_contract import TABLE_FIELDS as REVIEW_FIELDS
except ModuleNotFoundError:
    from recovery_review_contract import TABLE_FIELDS as REVIEW_FIELDS

INVENTORY_TABLES = (
    "ocr_jobs",
    "ocr_attempts",
    "proposal_jobs",
    "proposal_attempts",
    "assistant_turns",
    "intake_runs",
    "model_costs",
    "bridge_outbox",
    "delivery_receipt_items",
    "delivery_notifications",
    "account_invitation_deliveries",
    "notification_investigations",
    "tax_filings",
    "tax_filing_attachments",
    "delivery_provider_events",
    "handoff_tasks",
    "tax_profiles",
    "tax_workpapers",
    "monthly_delivery_packages",
    "monthly_delivery_grants",
    "monthly_delivery_acknowledgements",
    "monthly_delivery_opens",
    *REVIEW_FIELDS,
)

# Notification rows are not original-file references. Their exact recipient,
# frozen package and sending generation belong in the operational release proof.
# A pre-N03 schema cannot silently supply a partial release inventory.
DELIVERY_INVENTORY_FIELDS = (
    "id",
    "firm_id",
    "company_id",
    "book_id",
    "period",
    "package_id",
    "package_version",
    "manifest_sha256",
    "recipient_user_id",
    "grant_id",
    "grant_version",
    "kind",
    "dedupe_key",
    "state",
    "attempts",
    "worker_id",
    "lease_token",
    "lease_until",
    "send_started_at",
    "available_at",
    "restore_epoch",
    "payload_json",
    "sink_ref",
    "created_by",
    "created_at",
    "updated_at",
    "dispatched_at",
    "last_error",
)

# Tax and delivery rows are business evidence, not queue state. After a restore
# the operator must prove the same tax workpaper was prepared/reviewed (never
# infer tax filing), the same manifest was frozen and the same customer signed
# the same version. A pre-N04 schema cannot
# silently supply a partial inventory, so these columns are required verbatim.
BUSINESS_EVIDENCE_FIELDS = {
    # Requires forward migration 218. No plaintext activation secrets exist.
    "account_invitation_deliveries": tuple(
        "id request_id firm_id company_id request_attempt activation_digest recipient_email idempotency_key created_by created_at state finished_at channel".split()
    ),
    "tax_filings": tuple(
        "id firm_id company_id book_id period kind status external_ref occurred_at amount_cents evidence_note created_by created_at entry_kind supersedes_id workpaper_id workpaper_version workpaper_digest workpaper_status amount_check correction_reason".split()
    ),
    "tax_filing_attachments": tuple(
        "id filing_id firm_id company_id book_id storage_key filename media_type sha256 size_bytes created_by created_at".split()
    ),
    "delivery_provider_events": tuple(
        "id notification_id firm_id company_id book_id provider_event_id event_type occurred_at received_at payload_sha256 signature_timestamp raw_size".split()
    ),
    "handoff_tasks": tuple(
        "id firm_id source_kind source_id source_version source_fingerprint book_id role assignee_id due_on next_action status reason created_by created_at updated_at superseded_at".split()
    ),
    # Forward contract 202: manual assertions are evidence, never transport receipts.
    "notification_investigations": tuple(
        "id notification_id firm_id company_id book_id period package_id package_version manifest_sha256 recipient_user_id attempts revision conclusion evidence_ref reason idempotency_key created_by created_at".split()
    ),
    "tax_profiles": (
        "id",
        "firm_id",
        "company_id",
        "effective_from",
        "taxpayer_type",
        "vat_rate_bp",
        "surtax_rate_bp",
        "filing_cycle",
        "reason",
        "created_by",
        "created_at",
    ),
    "tax_workpapers": (
        "id",
        "firm_id",
        "company_id",
        "book_id",
        "period",
        "version",
        "status",
        "profile_id",
        "output_net_cents",
        "output_tax_cents",
        "input_net_cents",
        "input_tax_cents",
        "tax_cents",
        "surtax_cents",
        "credit_carry_cents",
        "ledger_cents",
        "variance_cents",
        "variance_reason",
        "lines_json",
        "source_digest",
        "source_snapshot_json",
        "created_by",
        "created_at",
        "reviewed_by",
        "reviewed_at",
        "review_reason",
    ),
    "monthly_delivery_packages": (
        "id",
        "book_id",
        "firm_id",
        "company_id",
        "period",
        "version",
        "report_id",
        "report_version",
        "contact_user_id",
        "service_plan_version",
        "manifest_sha256",
        "snapshot_json",
        "idempotency_key",
        "request_fingerprint",
        "reason",
        "created_by",
        "created_at",
    ),
    "monthly_delivery_grants": (
        "id",
        "firm_id",
        "company_id",
        "book_id",
        "period",
        "recipient_user_id",
        "version",
        "state",
        "granted_by",
        "granted_at",
        "revoked_by",
        "revoked_at",
        "reason",
    ),
    "monthly_delivery_acknowledgements": (
        "package_id",
        "book_id",
        "firm_id",
        "company_id",
        "manifest_sha256",
        "reason",
        "created_by",
        "created_at",
    ),
    "monthly_delivery_opens": (
        "package_id",
        "book_id",
        "firm_id",
        "company_id",
        "opened_by",
        "opened_at",
    ),
}
REQUIRED_COLUMNS = {
    "delivery_notifications": DELIVERY_INVENTORY_FIELDS,
    **BUSINESS_EVIDENCE_FIELDS,
    **{table: tuple(fields.split()) for table, fields in REVIEW_FIELDS.items()},
}


def pending_inventory(conn):
    """Content-bound operational evidence, without prompts, payloads or secrets."""
    from psycopg import sql

    inventory = {}
    for table in INVENTORY_TABLES:
        columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='accounting' AND table_name=%s",
                (table,),
            )
        }
        fields = [
            field
            for field in (
                "id",
                "receipt_id",
                "ordinal",
                "firm_id",
                "company_id",
                "intake_id",
                "attachment_id",
                "status",
                "state",
                "phase",
                "version",
                "attempt_no",
                "attempt_count",
                "generation",
                "reserved_micro",
                "estimated_micro",
                "confirmed_micro",
                "input_tokens",
                "output_tokens",
                "charge_token",
                "pricing_json",
                "sent_at",
                "lease_token",
                "lease_until",
                "raw_json",
            )
            if field in columns
        ]
        if table in REQUIRED_COLUMNS:
            required = REQUIRED_COLUMNS[table]
            missing = sorted(set(required) - columns)
            if missing:
                if table == "delivery_notifications":
                    raise ValueError("恢复通知清单缺少绑定、发送或恢复版本字段，必须先完成核验")
                raise ValueError(
                    f"恢复清单缺少 {table} 的绑定、版本或证据字段（{','.join(missing)}），必须先完成核验"
                )
            fields = list(required)
        if not fields:
            raise ValueError("恢复在途清单缺少必要运行表")
        rows = conn.execute(
            sql.SQL("SELECT {} FROM accounting.{}").format(
                sql.SQL(",").join(map(sql.Identifier, fields)), sql.Identifier(table)
            )
        ).fetchall()
        # Include all states: converting uncertain into succeeded/released also
        # changes the digest. Receipt payloads are hashed, never exposed.
        normalized = []
        for row in rows:
            item = dict(zip(fields, row))
            if table == "account_invitation_deliveries":
                item["recipient_email"] = hashlib.sha256(
                    str(item["recipient_email"]).encode()
                ).hexdigest()
            if table == "notification_investigations":
                for name in ("evidence_ref", "reason"):
                    item[name] = hashlib.sha256(str(item[name]).encode()).hexdigest()
            for name in (
                "raw_json",
                "lease_token",
                "charge_token",
                "pricing_json",
                "payload_json",
                "sink_ref",
                "last_error",
                "snapshot_json",
                "lines_json",
                "source_snapshot_json",
            ):
                if name in item and item[name] is not None:
                    item[name] = hashlib.sha256(str(item[name]).encode()).hexdigest()
            normalized.append(item)
        inventory[table] = sorted(normalized, key=lambda value: json.dumps(value, sort_keys=True))
    return {"tables": inventory, "sha256": digest(inventory)}


def release_local_hold(database, restore_directory, *, operator, reason, checks, inventory_sha256):
    import psycopg

    target = urlsplit(database)
    if not (
        target.scheme in {"postgres", "postgresql"}
        and target.hostname == "127.0.0.1"
        and target.port
        and target.port != 5432
        and target.path.startswith("/ak01_local_ak05_")
        and not target.query
        and not target.fragment
    ):
        raise ValueError("此工具只允许明确的本机隔离恢复库，不支持生产释放")
    if set(checks) != REQUIRED_CHECKS or any(value is not True for value in checks.values()):
        raise ValueError("数据库、原件、身份、权限、业务与未知外发核对必须全部完成")
    if (
        not isinstance(operator, str)
        or not operator.strip()
        or not isinstance(reason, str)
        or not reason.strip()
    ):
        raise ValueError("释放须记录真实操作人和原因")
    directory = Path(restore_directory).resolve()
    marker = directory / "restore-verification.json"
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("恢复隔离标记不存在或不安全")
    receipt = json.loads(marker.read_text(encoding="utf-8"))
    if receipt.get("security_verified") is not True or not receipt.get("restore_id"):
        raise ValueError("恢复的权限清单尚未验证")
    with psycopg.connect(database) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (6_420_031_786_281_903_101,))
        hold = conn.execute(
            "SELECT restore_id,hold FROM accounting.restore_state WHERE instance_id='restored' FOR UPDATE"
        ).fetchone()
        if not hold or hold != (receipt["restore_id"], True):
            raise ValueError("数据库 hold 与本次恢复标记不一致")
        verify_security_owners(conn)
        verify_sequences(conn)
        if conn.execute(
            "SELECT count(*) FROM accounting.restore_state WHERE hold AND instance_id<>'restored'"
        ).fetchone()[0]:
            raise ValueError("仍有其他恢复隔离记录，不得释放")
        security_hash = hashlib.sha256(
            json.dumps(capture_security(conn), sort_keys=True).encode()
        ).hexdigest()
        if security_hash != receipt.get("security_manifest_sha256"):
            raise ValueError("恢复核验后数据库权限或结构发生变化")
        inventory = pending_inventory(conn)
        if inventory["sha256"] != inventory_sha256:
            raise ValueError("在途任务、费用或外部回执已变化，必须重新核对")
        proof = {
            "checks": checks,
            "local_drill_only": True,
            "outbound_disabled": True,
            "worker_autostart": False,
            "inventory_sha256": inventory_sha256,
        }
        stamp = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO accounting.recovery_release_audit(restore_id,operator,reason,proof_json,inventory_sha256,created_at) VALUES(%s,%s,%s,%s,%s,%s)",
            (
                receipt["restore_id"],
                operator,
                reason,
                json.dumps(proof, sort_keys=True),
                inventory_sha256,
                stamp,
            ),
        )
        conn.execute(
            "UPDATE accounting.restore_state SET hold=FALSE,reason=%s,updated_by=%s,updated_at=%s WHERE instance_id='restored'",
            (reason, operator, stamp),
        )
    # DB first. Failure to rename is conservative: startup still sees the file
    # hold; operators may investigate the committed audit rather than rerun.
    marker.rename(directory / "restore-released.json")
    return {"restore_id": receipt["restore_id"], "released_local_only": True, **proof}


def main():
    import argparse
    import os
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-env", required=True)
    parser.add_argument("--restore-directory", required=True)
    parser.add_argument("--proof", required=True)
    parser.add_argument("--operator", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--allow-local-drill", action="store_true", required=True)
    args = parser.parse_args()
    try:
        proof = json.loads(Path(args.proof).read_text(encoding="utf-8"))
        result = release_local_hold(
            os.environ[args.database_env],
            args.restore_directory,
            operator=args.operator,
            reason=args.reason,
            checks=proof["checks"],
            inventory_sha256=proof["inventory_sha256"],
        )
        print(json.dumps(result, ensure_ascii=False))
    except Exception:
        print(
            "恢复隔离未释放：请检查本地目标、核对证明、当前权限和在途清单；连接信息未输出",
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
