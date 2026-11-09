"""Explicit reconciliation of immutable asset history, through existing review."""

import json

from ..accounting.domain import AccountingError
from .service import _fingerprint, _month_index


def preview(service, conn, actor, asset_id, book_id):
    book, ledger = service._book(conn, actor, book_id)
    asset = conn.execute("SELECT * FROM periodic_assets WHERE id=?", (asset_id,)).fetchone()
    if not asset:
        raise AccountingError("资产不存在或不可访问", 404)
    origin, origin_ledger = service._book(conn, actor, asset["book_id"])
    if (
        origin_ledger != ledger
        or origin["company_id"] != book["company_id"]
        or origin["period"] > book["period"]
    ):
        raise AccountingError("资产不属于当前企业、长期账套及期间", 404)
    service._asset_history_access(conn, actor, asset, book, ledger)
    row = conn.execute(
        "SELECT source_json FROM asset_reconciliation_sources WHERE asset_id=? AND book_id=?",
        (asset_id, book_id),
    ).fetchone()
    basis = json.loads(row[0])
    blockers = []
    if asset["status"] != "active" or asset["disposed_period"]:
        blockers.append("资产已处置，请从原记录核对处置依据")
    if _month_index(book["period"]) < _month_index(asset["start_period"]):
        blockers.append("尚未到开始计提月份")
    accounts = []
    for name in ("expense", "accum"):
        identifier, version = basis[name + "_account_id"], basis[name + "_version"]
        try:
            current = service._account(conn, ledger, identifier, version, book["period"])
            original = conn.execute(
                "SELECT * FROM ledger_account_versions WHERE account_id=? AND version=?",
                (identifier, asset[name + "_account_version"]),
            ).fetchone()
            if any(current[k] != original[k] for k in ("code", "category", "normal_balance")):
                blockers.append(
                    "科目性质或编码已变化，需要先人工办理重分类；不能仅更新版本继续计提"
                )
            accounts.append(
                {
                    "id": identifier,
                    "version": version,
                    "original_version": asset[name + "_account_version"],
                    "name": current["name"],
                }
            )
        except AccountingError as error:
            blockers.append(str(error))
    for history in basis["history"]:
        service.accounting.access.book(conn, actor, history["book_id"], "read")
        if (
            history["period"] >= book["period"]
            or history["unsupported"]
            or history["net_cents"] != history["accum_cents"]
        ):
            blockers.append("历史调整包含其他科目、辅助项或未来期间，请先人工核对资产归属")
    net = basis["net_posted_cents"]
    maximum = asset["cost_cents"] - asset["residual_cents"]
    elapsed = _month_index(book["period"]) - _month_index(asset["start_period"]) + 1
    cumulative = (
        maximum
        if elapsed >= asset["life_months"]
        else maximum // asset["life_months"] * max(elapsed, 0)
    )
    amount = cumulative - net
    if net < 0 or net > maximum or amount <= 0:
        blockers.append("当前差额不适合继续计提，请核对正式账；不会自动冲减或造零金额凭证")
    existing = conn.execute(
        "SELECT id,status,draft_id,input_fingerprint FROM periodic_runs WHERE asset_id=? AND period=? AND status<>'void'",
        (asset_id, book["period"]),
    ).fetchone()
    if existing and existing["status"] == "posted":
        blockers.append("本期已记账，请从原凭证更正，并在后续开放期间核对")
    if conn.execute(
        "SELECT 1 FROM periodic_runs WHERE asset_id=? AND period<>? AND status<>'void' AND (period>? OR status<>'posted')",
        (asset_id, book["period"], book["period"]),
    ).fetchone():
        blockers.append("前期草稿未完成或已有后续记录，请先办理原记录")
    locked = book["period_status"] != "open" or bool(
        conn.execute(
            "SELECT 1 FROM internal_period_locks WHERE book_id=? AND period=?",
            (book_id, book["period"]),
        ).fetchone()
    )
    if locked:
        blockers.append("本期已锁定，请选择后续开放期间")
    return {
        "scope": {
            "firm_id": book["firm_id"],
            "company_id": book["company_id"],
            "book_id": book_id,
            "period": book["period"],
        },
        "asset_id": asset_id,
        "asset_name": asset["name"],
        "basis": basis,
        "fingerprint": _fingerprint(basis),
        "history": basis["history"],
        "accounts": accounts,
        "prior_posted_cents": service._asset_prior_posted(conn, asset_id, book["period"]),
        "net_posted_cents": net,
        "amount_cents": amount,
        "cumulative_cents": cumulative,
        "blockers": list(dict.fromkeys(blockers)),
        "can_generate": bool(book["permissions"]["review"]) and not blockers,
        "replaces_draft_id": existing["draft_id"]
        if existing and existing["status"] != "posted"
        else None,
    }


def generate(service, conn, actor, asset_id, request):
    book, _ = service._book(conn, actor, request.book_id, write=True)
    view = preview(service, conn, actor, asset_id, book["id"])
    if request.expected_fingerprint != view["fingerprint"]:
        raise AccountingError("核对依据已变化，请重新预览差额", 409)
    if not view["can_generate"]:
        raise AccountingError("；".join(view["blockers"]) or "当前无办理权限", 409)
    key = f"asset-reconciliation:{asset_id}:{book['period']}:{view['fingerprint']}"
    previous = service._run(conn, book["id"], key)
    if previous and previous["status"] != "void":
        return service._draft_view(conn, previous["draft_id"], replayed=True)
    if view["replaces_draft_id"]:
        conn.execute(
            "UPDATE periodic_runs SET status='void' WHERE draft_id=? AND status IN ('generated','approved')",
            (view["replaces_draft_id"],),
        )
    asset = conn.execute("SELECT * FROM periodic_assets WHERE id=?", (asset_id,)).fetchone()
    basis = view["basis"]
    lines = [
        {
            "account_id": basis["expense_account_id"],
            "account_version": basis["expense_version"],
            "debit_cents": view["amount_cents"],
            "credit_cents": 0,
        },
        {
            "account_id": basis["accum_account_id"],
            "account_version": basis["accum_version"],
            "credit_cents": view["amount_cents"],
            "debit_cents": 0,
        },
    ]
    result = service._insert_draft(
        conn,
        actor,
        book,
        kind=asset["kind"],
        run_key=key,
        asset_id=asset_id,
        description="核对后计提：" + asset["name"],
        lines=lines,
        fingerprint=view["fingerprint"],
        reconciliation_json=json.dumps(basis, ensure_ascii=False),
    )
    service._audit(
        conn,
        actor,
        book["id"],
        "periodic.asset.reconcile",
        result["draft"]["id"],
        {
            "reason": request.reason,
            "fingerprint": view["fingerprint"],
            "replaces_draft_id": view["replaces_draft_id"],
            "amount_cents": view["amount_cents"],
        },
    )
    return result
