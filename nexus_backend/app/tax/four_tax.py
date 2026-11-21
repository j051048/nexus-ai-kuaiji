"""Four-tax period package: glue between the reviewed VAT workpaper and the engines.

The VAT workpaper remains the single source of truth for output/input tax and
carry-over. This module does **not** recompute VAT from invoices a second way;
it takes the authoritative reviewed cents and derives the dependent taxes:

* surtax  — computed on the VAT actually payable (not on the credit),
* CIT     — prepayment on the caller-supplied cumulative profit,
* stamp   — per-contract, refusing duplicates.

Anything missing (no reviewed workpaper, no surtax policy item) is refused
with a 409 that says what to fix, never a silent zero.
"""

from ..accounting.domain import AccountingError

from . import engines

SUPPORTED = ("vat", "surtax", "cit_prepayment", "stamp_duty")


def _latest_reviewed(conn, book_id, period):
    return conn.execute(
        "SELECT id,version,tax_cents,surtax_cents,credit_carry_cents,ledger_cents,"
        "source_digest,status FROM tax_workpapers WHERE book_id=? AND period=? "
        "AND status='reviewed' ORDER BY version DESC LIMIT 1",
        (book_id, period),
    ).fetchone()


def package(
    service,
    actor,
    book_id,
    period,
    *,
    surtax_items,
    cumulative_profit_cents=None,
    adjustments=(),
    loss_carryforward_cents=0,
    prepaid_cents=0,
    cit_rate_bp=2_500,
    contracts=(),
    stamp_rates=None,
    already_counted=(),
    halved=False,
):
    """Compose the four taxes for one period from the reviewed VAT workpaper."""
    with service.db.transaction(actor=actor) as conn:
        service.access.book(conn, actor, book_id, "read")
        row = _latest_reviewed(conn, book_id, period)
    if row is None:
        raise AccountingError(
            f"{period}没有已复核的增值税底稿，无法计算附加税与企业所得税预缴；"
            "请先生成并完成独立复核",
            409,
        )
    vat_cents = int(row["tax_cents"])
    surtax = engines.surtax(vat_cents, surtax_items, halved=halved)
    profit = int(
        row["ledger_cents"] if cumulative_profit_cents is None else cumulative_profit_cents
    )
    cit = engines.cit_prepayment(
        cumulative_profit_cents=profit,
        adjustments=adjustments,
        loss_carryforward_cents=loss_carryforward_cents,
        prepaid_cents=prepaid_cents,
        rate_bp=cit_rate_bp,
    )
    stamp = engines.stamp_duty(contracts, stamp_rates or {}, already_counted=already_counted)
    return {
        "period": period,
        "source": {
            "kind": "tax_workpaper",
            "id": row["id"],
            "version": int(row["version"]),
            "status": row["status"],
            "digest": row["source_digest"],
        },
        "vat": {
            "tax_cents": vat_cents,
            "credit_carry_cents": int(row["credit_carry_cents"]),
            "surtax_cents_recorded": int(row["surtax_cents"]),
        },
        "surtax": surtax,
        "cit_prepayment": cit,
        "stamp_duty": stamp,
        "kinds": list(SUPPORTED),
    }
