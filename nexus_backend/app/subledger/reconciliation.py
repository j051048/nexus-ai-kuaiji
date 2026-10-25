"""Exact auxiliary buckets over existing formal opening and posted lines.

This module reads accounting facts; it neither posts journals nor nets away
unresolved evidence. Historical entity labels are not revalidated on reads.
"""

import json
from collections import Counter, defaultdict

from ..accounting.domain import AccountingError
from ..accounting.invariants import money_to_cents
from ..posting.service import PostingService


def auxiliary(raw):
    value = json.loads(raw or "{}") if isinstance(raw, str) else raw
    if not isinstance(value, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()
    ):
        raise AccountingError("历史辅助维度格式异常，请复核原始事实", 409)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def settlement_side(kind):
    return "credit_cents" if kind in {"receivable", "advance_paid"} else "debit_cents"


def formal_buckets(conn, book_id, as_of=None, *, opening_only=False):
    # _binding validates the frozen opening fingerprint and uses no service state.
    binding = PostingService._binding(None, conn, book_id)
    buckets = defaultdict(int)
    for row in conn.execute(
        "SELECT account_id,aux_json,opening_cents FROM opening_lines WHERE package_version_id=?",
        (binding["package_version_id"],),
    ):
        buckets[(row["account_id"], auxiliary(row["aux_json"]))] += int(row["opening_cents"])
    if not opening_only:
        sql = "SELECT l.account_id,l.aux_json,l.debit_cents,l.credit_cents FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id=?"
        params = [book_id]
        if as_of:
            sql += " AND v.voucher_date<=?"
            params.append(as_of)
        for row in conn.execute(sql, params):
            buckets[(row["account_id"], auxiliary(row["aux_json"]))] += int(
                row["debit_cents"]
            ) - int(row["credit_cents"])
    return dict(buckets)


def control_rows(service, conn, book, configs, as_of=None):
    # One account cannot stand for two different control natures in the same
    # period: otherwise the same formal balance would be counted twice.
    kinds = defaultdict(set)
    required = {}
    for config in configs:
        kinds[config["control_account_id"]].add(config["kind"])
        required[(config["kind"], config["control_account_id"])] = json.loads(
            config["required_aux_json"]
        )
    if any(len(values) > 1 for values in kinds.values()):
        return {
            "status": "blocked",
            "reason": "同一控制科目有重叠核算类型，请明确配置",
            "rows": [],
            "passed": False,
        }
    formal = formal_buckets(conn, book["id"], as_of)
    amounts = defaultdict(int)
    sql = "SELECT * FROM subledger_items WHERE book_id=? AND status IN('open','partial','settled')"
    params = [book["id"]]
    if as_of:
        sql += " AND original_date<=?"
        params.append(as_of)
    for item in conn.execute(sql, params):
        remaining = int(item["amount_cents"]) - service._remaining(conn, item["id"], as_of)
        if remaining < 0:
            raise AccountingError("历史核销超过未结事项金额，请复核", 409)
        amounts[(item["kind"], item["control_account_id"], auxiliary(item["aux_json"]))] += (
            remaining
        )
    keys = set(amounts)
    for account_id, group in kinds.items():
        kind = next(iter(group))
        keys.update(
            (kind, account_id, aux)
            for (acc, aux), value in formal.items()
            if acc == account_id and value
        )
        if not any(key[1] == account_id for key in keys):
            keys.add((kind, account_id, "{}"))
    rows = []
    for kind, account_id, aux in sorted(keys):
        amount = amounts.get((kind, account_id, aux), 0)
        book_amount = formal.get((account_id, aux), 0)
        expected = amount if kind in {"receivable", "advance_paid"} else -amount
        dimensions = json.loads(aux)
        configured = (kind, account_id) in required
        dimensions_valid = not (amount or book_amount) or set(
            required.get((kind, account_id), [])
        ).issubset(dimensions)
        rows.append(
            {
                "kind": kind,
                "control_account_id": account_id,
                "aux": dimensions,
                "required_aux": required.get((kind, account_id), []),
                "dimensions_valid": dimensions_valid,
                "subledger_cents": amount,
                "book_cents": book_amount,
                "difference_cents": book_amount - expected,
                "passed": configured and dimensions_valid and book_amount == expected,
            }
        )
    return {
        "status": "configured",
        "reason": "",
        "rows": rows,
        "passed": all(row["passed"] for row in rows),
    }


def unresolved_adjustments(conn, book_id):
    # Legacy statuses alone are not independent proof. Nonzero adjustments
    # remain outstanding until a scoped resolution binds actual matched lines.
    return [
        dict(row)
        for row in conn.execute(
            "SELECT a.* FROM bank_recon_adjustments a WHERE a.book_id=? AND a.amount_cents<>0 "
            "AND NOT EXISTS(SELECT 1 FROM bank_adjustment_resolutions r WHERE r.adjustment_id=a.id) ORDER BY a.created_at,a.id",
            (book_id,),
        )
    ]


def bank_line_evidence(conn, book, bank_account_id=None, key=None):
    formal = {
        row["id"]: dict(row)
        for row in conn.execute(
            "SELECT l.* FROM voucher_lines l JOIN vouchers v ON v.id=l.voucher_id WHERE v.book_id=?",
            (book["id"],),
        )
        if (row["account_id"], auxiliary(row["aux_json"])) == key
        or (key is None and row["code_snapshot"].startswith(("1001", "1002")))
    }
    statements = conn.execute(
        "SELECT * FROM bank_statement_lines WHERE book_id=?"
        + (" AND bank_account_id=?" if bank_account_id else ""),
        (book["id"], bank_account_id) if bank_account_id else (book["id"],),
    ).fetchall()
    matched = set()
    invalid = 0
    for statement in statements:
        if statement["status"] != "matched":
            continue
        line = formal.get(statement["matched_voucher_line_id"])
        if (
            not line
            or line["id"] in matched
            or int(line["debit_cents"]) - int(line["credit_cents"])
            != int(statement["amount_cents"])
            or statement["period"] != book["period"]
            or statement["line_date"][:7] != book["period"]
        ):
            invalid += 1
        else:
            matched.add(line["id"])
    return {
        "invalid_match_count": invalid,
        "unmatched_formal_count": sum(
            1
            for line_id, line in formal.items()
            if line_id not in matched and int(line["debit_cents"]) - int(line["credit_cents"])
        ),
    }


def statement_source_evidence(conn, book, account, coverage):
    rows = conn.execute(
        "SELECT line_date,amount_cents FROM bank_statement_lines WHERE book_id=? AND bank_account_id=?",
        (book["id"], account["id"]),
    ).fetchall()
    actual = Counter((row["line_date"], int(row["amount_cents"])) for row in rows)
    if not coverage or not coverage["source_preview_id"]:
        return {"statement_source_matches": False, "source_line_count": None}
    preview = conn.execute(
        "SELECT p.payload_json FROM bank_import_previews p JOIN bank_import_confirmations c ON c.preview_id=p.id AND c.book_id=p.book_id JOIN bank_import_files f ON f.id=p.file_id AND f.book_id=p.book_id WHERE p.id=? AND p.book_id=? AND f.id=? AND f.sha256=?",
        (
            coverage["source_preview_id"],
            book["id"],
            coverage["source_file_id"],
            coverage["source_sha256"],
        ),
    ).fetchone()
    if not preview:
        return {"statement_source_matches": False, "source_line_count": None}
    payload = json.loads(preview["payload_json"])
    if payload["request"]["bank_account"] != account["id"] or any(
        row["errors"] for row in payload["rows"]
    ):
        return {"statement_source_matches": False, "source_line_count": None}
    expected = Counter(
        (
            row["source"]["date"],
            money_to_cents(row["source"]["amount"])
            * (1 if row["source"]["direction"] == "income" else -1),
        )
        for row in payload["rows"]
    )
    return {
        "statement_source_matches": expected == actual,
        "source_line_count": sum(expected.values()),
    }


def bank_rows(service, conn, actor, book, trial):
    accounts = conn.execute(
        "SELECT * FROM bank_accounts WHERE book_id=? ORDER BY name,id", (book["id"],)
    ).fetchall()
    adjustments = unresolved_adjustments(conn, book["id"])
    extra = {
        "unresolved_adjustment_count": len(adjustments),
        "unresolved_adjustment_absolute_cents": sum(
            abs(int(x["amount_cents"])) for x in adjustments
        ),
        "unexplained_cents": sum(int(x["amount_cents"]) for x in adjustments),
    }
    if not accounts:
        totals = conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(amount_cents),0),COALESCE(SUM(CASE WHEN status<>'matched' THEN 1 ELSE 0 END),0),COALESCE(SUM(CASE WHEN status<>'matched' THEN amount_cents ELSE 0 END),0) FROM bank_statement_lines WHERE book_id=?",
            (book["id"],),
        ).fetchone()
        cash = [r for r in trial["rows"] if r["is_leaf"] and r["code"].startswith(("1001", "1002"))]
        closing = sum(int(r["closing_cents"]) for r in cash)
        opening = sum(int(r["opening_cents"]) for r in cash)
        difference = closing - opening - int(totals[1])
        invalid = conn.execute(
            "SELECT COUNT(*) FROM bank_statement_lines s LEFT JOIN voucher_lines l ON l.id=s.matched_voucher_line_id LEFT JOIN vouchers v ON v.id=l.voucher_id WHERE s.book_id=? AND s.status='matched' AND (l.id IS NULL OR v.book_id<>s.book_id OR (l.code_snapshot NOT LIKE ? AND l.code_snapshot NOT LIKE ?) OR l.debit_cents-l.credit_cents<>s.amount_cents)",
            (book["id"], "1001%", "1002%"),
        ).fetchone()[0]
        evidence = bank_line_evidence(conn, book)
        cash_ids = {row["account_id"] for row in cash}
        bank_facts = any(
            any(
                int(row[field])
                for field in ("opening_cents", "debit_cents", "credit_cents", "closing_cents")
            )
            for row in cash
        ) or any(
            amount
            for (account_id, _), amount in formal_buckets(
                conn, book["id"], opening_only=True
            ).items()
            if account_id in cash_ids
        )
        coverage_ok = not bank_facts and not invalid
        return {
            **extra,
            **evidence,
            "bank_account_required": bool(bank_facts),
            "statement_cents": int(totals[1]),
            "statement_line_count": int(totals[0]),
            "book_cash_cents": closing,
            "opening_cash_cents": opening,
            "unmatched_count": int(totals[2]),
            "unmatched_cents": int(totals[3]),
            "adjustments": adjustments,
            "difference_cents": difference,
            "coverage_ok": coverage_ok,
            "passed": coverage_ok
            and not adjustments
            and int(totals[2]) == 0
            and difference == 0
            and not any(evidence.values()),
            "identity_status": "legacy_unbound",
            "continuity_not_verified": True,
        }
    formal = formal_buckets(conn, book["id"])
    formal_opening = formal_buckets(conn, book["id"], opening_only=True)
    mapped = {}
    duplicates = set()
    for account in accounts:
        key = (account["ledger_account_id"], auxiliary(account["aux_json"]))
        if key in mapped:
            duplicates.add(key)
        mapped[key] = account["id"]
    cash_ids = {
        r["account_id"]
        for r in trial["rows"]
        if r["is_leaf"] and r["code"].startswith(("1001", "1002"))
    }
    unmapped = [
        (key, value)
        for key, value in formal.items()
        if key[0] in cash_ids and value and key not in mapped
    ]
    unassigned = conn.execute(
        "SELECT COUNT(*) FROM bank_statement_lines WHERE book_id=? AND bank_account_id IS NULL",
        (book["id"],),
    ).fetchone()[0]
    rows = []
    for account in accounts:
        key = (account["ledger_account_id"], auxiliary(account["aux_json"]))
        coverage = conn.execute(
            "SELECT * FROM bank_statement_coverages WHERE account_id=? AND book_id=? AND period=? ORDER BY CASE WHEN state IN('pending_review','verified') THEN 0 ELSE 1 END,created_at DESC,id DESC LIMIT 1",
            (account["id"], book["id"], book["period"]),
        ).fetchone()
        lines = conn.execute(
            "SELECT COUNT(*),COALESCE(SUM(amount_cents),0),COALESCE(SUM(CASE WHEN status<>'matched' THEN 1 ELSE 0 END),0) FROM bank_statement_lines WHERE book_id=? AND bank_account_id=?",
            (book["id"], account["id"]),
        ).fetchone()
        opening = int(coverage["opening_cents"]) if coverage else 0
        closing = int(coverage["closing_cents"]) if coverage else 0
        accounted = formal.get(key, 0)
        verified = bool(coverage and coverage["state"] == "verified")
        evidence = bank_line_evidence(conn, book, account["id"], key)
        source_evidence = statement_source_evidence(conn, book, account, coverage)
        passed = (
            verified
            and bool(account["active"])
            and key not in duplicates
            and bool(key[0])
            and opening + int(lines[1]) == closing
            and accounted == closing
            and int(lines[2]) == 0
            and opening == formal_opening.get(key, 0)
            and not any(evidence.values())
            and source_evidence["statement_source_matches"]
        )
        rows.append(
            {
                **evidence,
                **source_evidence,
                "account_id": account["id"],
                "name": account["name"],
                "ledger_account_id": account["ledger_account_id"],
                "aux": json.loads(key[1]),
                "mapping_ambiguous": key in duplicates,
                "coverage": verified,
                "coverage_state": coverage["state"] if coverage else "missing",
                "opening_cents": opening,
                "net_cents": int(lines[1]),
                "closing_cents": closing,
                "book_cents": accounted,
                "line_count": int(lines[0]),
                "unmatched_count": int(lines[2]),
                "difference_cents": opening + int(lines[1]) - closing,
                "book_difference_cents": accounted - closing,
                "opening_book_difference_cents": formal_opening.get(key, 0) - opening,
                "passed": passed,
            }
        )
    bank_total = sum(formal.get(key, 0) for key in mapped)
    coverage_total = sum(row["closing_cents"] for row in rows)
    cash_total = bank_total + sum(value for _, value in unmapped)
    difference = cash_total - coverage_total
    return {
        **extra,
        "multi_account": True,
        "rows": rows,
        "coverage_ok": all(r["coverage"] for r in rows),
        "unmatched_count": sum(row["unmatched_count"] for row in rows),
        "statement_line_count": sum(row["line_count"] for row in rows),
        "unassigned_line_count": int(unassigned),
        "unmapped_cash_bucket_count": len(unmapped),
        "book_cash_cents": cash_total,
        "coverage_closing_cents": coverage_total,
        "difference_cents": difference,
        "passed": not adjustments
        and not unassigned
        and not unmapped
        and not duplicates
        and difference == 0
        and all(r["passed"] for r in rows),
        "identity_status": "legacy_unbound",
        "continuity_not_verified": True,
    }
