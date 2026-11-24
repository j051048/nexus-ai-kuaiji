"""Four-tax period package: glue between the reviewed VAT workpaper and the engines.

The VAT workpaper remains the single source of truth for output/input tax and
carry-over. This module does **not** recompute VAT from invoices a second way;
it takes the authoritative reviewed cents and derives the dependent taxes:

* surtax  — computed on the VAT actually payable (not on the credit),
* CIT     — prepayment on the caller-supplied cumulative profit,
* stamp   — per-contract, refusing duplicates.

Anything missing (no reviewed workpaper, no surtax policy item, no cumulative
profit) is refused with a 409 that says what to fix, never a silent zero.
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
    stamp_contracts=None,
    stamp_zero_confirmed=False,
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
    if cumulative_profit_cents is None:
        # ``ledger_cents`` on the workpaper is the *tax amount on the books*, not a
        # profit. Using it as the CIT base would accrue income tax on a number that
        # was never a profit, so the caller must state the cumulative profit.
        raise AccountingError(
            "计提企业所得税预缴必须提供本期累计利润（cumulative_profit_cents）；"
            "底稿的账载税额不能代替利润",
            409,
        )
    profit = int(cumulative_profit_cents)
    cit = engines.cit_prepayment(
        cumulative_profit_cents=profit,
        adjustments=adjustments,
        loss_carryforward_cents=loss_carryforward_cents,
        prepaid_cents=prepaid_cents,
        rate_bp=cit_rate_bp,
    )
    provided = list(stamp_contracts) if stamp_contracts is not None else list(contracts)
    if stamp_zero_confirmed and provided:
        raise AccountingError(
            "已确认本期无应税合同（零业务），不能再录入印花税合同明细；"
            "如确有应税合同，请取消零业务确认后重新计提",
            422,
        )
    if provided:
        stamp = engines.stamp_duty(provided, stamp_rates or {}, already_counted=already_counted)
        stamp = {
            "status": "computed",
            "accrued": True,
            "notice": None,
            "total_cents": stamp["total_cents"],
            "lines": stamp["lines"],
        }
    elif stamp_zero_confirmed:
        stamp = {
            "status": "zero_confirmed",
            "accrued": True,
            "notice": "已确认本期无应税合同（零业务）",
            "total_cents": 0,
            "lines": [],
        }
    else:
        # 没收到合同资料不等于本期没有应税合同：给 0 会让会计以为已经算过。
        stamp = {
            "status": "unprovided",
            "accrued": False,
            "notice": "未提供合同资料，本次未计提印花税，不能视作本期零业务",
            "total_cents": None,
            "lines": [],
        }
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
