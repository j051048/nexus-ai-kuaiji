"""Shared version fences for lease-derived accounting drafts."""

from __future__ import annotations

from ..accounting.domain import AccountingError


def assert_lease_source_current(conn, book_id: str, source_id: str, draft_id: str) -> None:
    """Reject a draft whose lease contract moved past its approved version.

    Lease drafts do not copy the contract version into the draft row. The
    authoritative relation is the source evidence key, so every review,
    posting and export boundary re-reads the contract under the same book.
    """
    source = conn.execute(
        "SELECT external_id FROM sources WHERE id=? AND book_id=?", (source_id, book_id)
    ).fetchone()
    if not source:
        return
    external_id = str(source["external_id"] or "")
    if not external_id.startswith(("lease:", "lease-refund:")):
        return
    contract_id = external_id.split(":", 2)[1]
    binding = conn.execute(
        "SELECT contract_id,contract_version FROM lease_draft_bindings WHERE draft_id=? AND book_id=?",
        (draft_id, book_id),
    ).fetchone()
    if not binding or binding["contract_id"] != contract_id:
        raise AccountingError(
            "租赁凭证缺少合同版本绑定，不能审核、记账或导出；请重新生成",
            409,
        )
    contract = conn.execute(
        "SELECT version,approved_version,status FROM lease_contracts WHERE id=? AND book_id=?",
        (contract_id, book_id),
    ).fetchone()
    if (
        not contract
        or contract["status"] != "active"
        or int(contract["approved_version"]) != int(contract["version"])
        or int(contract["version"]) != int(binding["contract_version"])
    ):
        raise AccountingError(
            "租赁合同已改版或终止，旧审批版本的凭证不能审核、记账或导出，请确认当前版本后重新生成",
            409,
        )
