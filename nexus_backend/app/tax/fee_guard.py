"""Shared source and contribution checks for ordinary review/export paths."""

import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def fee_context(conn, source, lines):
    batch = conn.execute(
        "SELECT b.* FROM tax_fee_batches b JOIN drafts d ON d.id=b.draft_id AND d.book_id=b.book_id "
        "WHERE d.source_id=? AND b.book_id=?", (source["id"], source["book_id"]),
    ).fetchone()
    if not batch:
        return False, []
    issues = []
    registered = (source["external_id"] == f"tax-fee:{batch['id']}"
                  and source["evidence_ref"] == f"tax-fee-batch:{batch['id']}"
                  and source["fingerprint"] == batch["batch_fingerprint"]
                  and source["amount_cents"] == batch["total_cents"])
    if not registered:
        issues.append("规费来源与批次身份不一致")
    if batch["status"] == "invalidated":
        issues.append("规费批次已失效，不能复核或导出")
    policy = json.loads(batch["policy_json"])
    if digest(policy) != batch["policy_fingerprint"]:
        issues.append("规费策略与冻结指纹不一致")
    snapshot = json.loads(batch["source_snapshot_json"])
    if not snapshot:
        issues.append("规费旧稿缺少完整来源快照，请重新核定")
    current = []
    for source_id, expected in snapshot:
        original = conn.execute(
            "SELECT * FROM tax_source_files WHERE id=? AND book_id=? AND firm_id=? AND company_id=?",
            (source_id, batch["book_id"], batch["firm_id"], batch["company_id"]),
        ).fetchone()
        if not original or original["status"] != "active" or original["fingerprint"] != expected:
            issues.append("规费来源已变化，旧审批与导出已失效")
            continue
        current.append((source_id, original["fingerprint"]))
        if any(row["status"] == "needs_review" for row in json.loads(original["rows_json"])):
            issues.append("规费来源仍有待核对行，请先处理来源差异")
    if digest(sorted(current)) != batch["source_fingerprint"]:
        issues.append("规费来源集合或版本与生成时不一致")
    rows = conn.execute("SELECT * FROM tax_fee_batch_rows WHERE batch_id=?", (batch["id"],)).fetchall()
    if any(row["status"] == "rejected" for row in rows):
        issues.append("规费批次仍有拒绝行，请核定来源后重新生成")
    expected_lines = {}
    for row in rows:
        if row["status"] != "adopted":
            continue
        for offset, role, account in ((0, "receivable", policy["driver_receivable_account"]),
                                      (1, "revenue", policy["vehicle_revenue_account"])):
            expected_lines[row["line_ordinal"] + offset] = (
                account, row["net_cents"] if offset == 0 else 0,
                row["net_cents"] if offset == 1 else 0,
                row["source_file_id"], row["source_row_number"], role)
    bindings = {row["ordinal"]: row for row in conn.execute(
        "SELECT * FROM draft_line_sources WHERE draft_id=? AND book_id=?",
        (batch["draft_id"], batch["book_id"]),
    ).fetchall()}
    if len(lines) != len(expected_lines) or len(bindings) != len(expected_lines):
        issues.append("规费分录与来源贡献行数不一致")
    for line in lines:
        expected, binding = expected_lines.get(line["ordinal"]), bindings.get(line["ordinal"])
        if (not expected or not binding or
            (line["account_code"], line["debit_cents"], line["credit_cents"],
             binding["source_file_id"], binding["source_row_number"], binding["role"]) != expected):
            issues.append("规费分录已修改或来源贡献不一致，须重新核定")
    return registered, list(dict.fromkeys(issues))
