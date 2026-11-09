"""Scoped read projection for human allocation; never creates payment vouchers."""

import json

from ..accounting.domain import AccountingError
from .reconciliation import auxiliary, settlement_side


def item_workspace(service, conn, actor, book_id, item_id):
    book, _ = service._book(conn, actor, book_id)
    row = conn.execute(
        "SELECT i.*,e.name AS counterparty_name FROM subledger_items i "
        "JOIN company_master_entities e ON e.id=i.counterparty_id AND e.company_id=i.company_id "
        "WHERE i.id=? AND i.book_id=?",
        (item_id, book["id"]),
    ).fetchone()
    if not row:
        raise AccountingError("往来事项不存在或不属于当前账套", 404)
    item = {**dict(row), **service._item_view(conn, item_id)}
    locked = book["period_status"] != "open" or bool(
        conn.execute(
            "SELECT 1 FROM internal_period_locks WHERE book_id=? AND period=?",
            (book["id"], book["period"]),
        ).fetchone()
    )
    histories = [
        dict(r)
        for r in conn.execute(
            "SELECT a.*,r.id AS reversal_id,r.reason AS reversal_reason,r.created_at AS reversed_at "
            "FROM subledger_allocations a LEFT JOIN subledger_allocation_reversals r ON r.allocation_id=a.id "
            "WHERE a.item_id=? AND a.book_id=? ORDER BY a.created_at,a.id",
            (item_id, book["id"]),
        )
    ]
    reasons = {}
    for audit in conn.execute(
        "SELECT details FROM audit_events WHERE book_id=? AND action='subledger.allocate'",
        (book["id"],),
    ):
        detail = json.loads(audit["details"])
        reasons[detail.get("idempotency_key")] = detail.get("reason", "历史记录未登记理由")
    for history in histories:
        history["reason"] = reasons.get(
            history["idempotency_key"].rsplit(":", 1)[0], "历史记录未登记理由"
        )
    side, used = settlement_side(item["kind"]), {}
    for allocation in conn.execute(
        "SELECT a.voucher_id,a.amount_cents,i.aux_json,i.kind FROM subledger_allocations a "
        "JOIN subledger_items i ON i.id=a.item_id WHERE a.book_id=? AND i.control_account_id=? "
        "AND NOT EXISTS(SELECT 1 FROM subledger_allocation_reversals r WHERE r.allocation_id=a.id)",
        (book["id"], item["control_account_id"]),
    ):
        if (
            auxiliary(allocation["aux_json"]) == auxiliary(item["aux_json"])
            and settlement_side(allocation["kind"]) == side
        ):
            used[allocation["voucher_id"]] = used.get(allocation["voucher_id"], 0) + int(
                allocation["amount_cents"]
            )
    vouchers = {}
    current_payment = (
        "accounting.subledger_payment_current(v.id)"
        if service.db.is_postgres
        else "v.reversal_of IS NULL AND NOT EXISTS(SELECT 1 FROM vouchers correction WHERE correction.reversal_of=v.id)"
    )
    for line in conn.execute(
        "SELECT v.id,v.voucher_date,v.description,l.aux_json,l.debit_cents,l.credit_cents "
        "FROM vouchers v JOIN voucher_lines l ON l.voucher_id=v.id "
        "WHERE v.book_id=? AND l.account_id=? AND v.voucher_date>=? "
        f"AND {current_payment} ORDER BY v.voucher_date,v.id",
        (book["id"], item["control_account_id"], item["original_date"]),
    ):
        if auxiliary(line["aux_json"]) != auxiliary(item["aux_json"]):
            continue
        value = vouchers.setdefault(
            line["id"],
            {
                "id": line["id"],
                "book_id": book["id"],
                "voucher_date": line["voucher_date"],
                "description": line["description"],
                "settlement_cents": 0,
            },
        )
        value["settlement_cents"] += int(line[side])
    available = []
    for voucher in vouchers.values():
        voucher["available_cents"] = voucher["settlement_cents"] - used.get(voucher["id"], 0)
        if voucher["available_cents"] > 0:
            available.append(voucher)
    # A declared reference is not proof of an original or of its review.
    source = {
        "kind": item["source_kind"],
        "id": item["source_id"],
        "version": item["source_version"],
        "verified": False,
    }
    if item["source_kind"] == "voucher" and item["source_id"]:
        source["verified"] = bool(
            conn.execute(
                "SELECT 1 FROM vouchers WHERE id=? AND book_id=? AND source_version=?",
                (item["source_id"], book["id"], item["source_version"]),
            ).fetchone()
        )
    result = {
        "scope": {
            "firm_id": book["firm_id"],
            "company_id": book["company_id"],
            "book_id": book["id"],
            "period": book["period"],
        },
        "item": item,
        "source": source,
        "allocations": histories,
        "vouchers": available,
        "locked": locked,
        "can_allocate": bool(book["permissions"]["review"]) and not locked,
    }
    from .service import _fingerprint

    result["fingerprint"] = _fingerprint(result)
    return result
