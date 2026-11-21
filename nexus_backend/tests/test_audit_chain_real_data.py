"""RWK05-B: audit hash-chain tamper evidence on real business data.

The existing OPT03 chain tests insert synthetic audit rows. This suite drives the
real service paths (firm/directory/accounting) so every row in the chain is
produced by business code, then breaks the chain three ways and proves the
verifier reports the exact broken row for the affected firm only.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.accounting.directory import DirectoryService  # noqa: E402
from app.accounting.models import (  # noqa: E402
    BookCreate,
    CompanyCreate,
    FirmCreate,
    FirmMemberGrant,
    ImportRequest,
)
from app.accounting.service import Actor  # noqa: E402
from scripts.verify_audit_chain import verify_sqlite  # noqa: E402
from test_directory import case as directory_case  # noqa: F401

ROWS_PER_FIRM = 50


def _firms(c):
    """Create a second firm with its own company so two chains coexist."""
    other = c.user("rwk05.b.owner")
    firm = c.directory.create_firm(other.user_id, FirmCreate(name="第二事务所"))
    actor = Actor(other.user_id, firm["id"])
    return c.actor, actor


def _seed(c, actor, prefix: str, count: int = ROWS_PER_FIRM) -> list[str]:
    """Drive real service paths until this firm owns `count` audit rows.

    Every row below comes from a public service method: company onboarding,
    firm member grants, book creation and a source import (which also creates the
    draft rows used later for review). Nothing writes audit_events directly.
    """
    ids: list[str] = []
    for index in range(30):
        company = c.directory.create_company(actor, CompanyCreate(name=f"{prefix}-{index:02d}"))
        ids.append(company["id"])
    for index in range(20):
        member = c.user(f"{prefix}.staff{index:02d}")
        c.directory.grant_firm_member(
            actor, FirmMemberGrant(email=member.email, role="operator")
        )
    book = c.service.create_book(actor, BookCreate(name=f"{prefix} 账套", period="2026-10", company_id=ids[0]))
    c.service.import_sources(
        actor,
        book["id"],
        ImportRequest(
            source_name=f"{prefix} 导入",
            rows=[
                {
                    "external_id": f"{prefix}-row-{i}",
                    "date": "2026-10-01",
                    "description": "办公用品",
                    "amount": "10.00",
                    "direction": "expense",
                    "evidence_ref": "测试发票",
                }
                for i in range(3)
            ],
        ),
    )
    return ids


def _rows(c):
    with c.db.transaction(actor=c.actor) as conn:
        return conn.execute(
            "SELECT * FROM audit_events ORDER BY firm_id,id"
        ).fetchall()


def _firm_rows(c, firm_id):
    return [row for row in _rows(c) if row["firm_id"] == firm_id]


def _drop_append_only(c):
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute("DROP TRIGGER IF EXISTS audit_events_append_only_update")
        conn.execute("DROP TRIGGER IF EXISTS audit_events_append_only_delete")


def test_two_firms_each_hold_fifty_real_rows_and_both_chains_verify(directory_case):
    c = directory_case
    firm_a, firm_b = _firms(c)
    _seed(c, firm_a, "A")
    _seed(c, firm_b, "B")

    rows_a = _firm_rows(c, firm_a.firm_id)
    rows_b = _firm_rows(c, firm_b.firm_id)
    assert len(rows_a) >= ROWS_PER_FIRM
    assert len(rows_b) >= ROWS_PER_FIRM
    # Every row carries a real chain link, not a NULL placeholder.
    assert all(row["row_hash"] and row["prev_hash"] is not None for row in rows_a + rows_b)
    # Distinct actions prove the rows came from different business paths.
    assert len({row["action"] for row in rows_a}) >= 2
    assert verify_sqlite(c.db.path) == []


@pytest.mark.parametrize("mode", ["content", "delete", "swap"])
def test_tampering_real_rows_is_reported_with_the_break_row(directory_case, mode):
    c = directory_case
    firm_a, firm_b = _firms(c)
    _seed(c, firm_a, "A")
    _seed(c, firm_b, "B")
    rows_a = _firm_rows(c, firm_a.firm_id)
    middle = rows_a[len(rows_a) // 2]
    _drop_append_only(c)

    with c.db.transaction(write=True, actor=c.actor) as conn:
        if mode == "content":
            conn.execute(
                "UPDATE audit_events SET details=? WHERE id=?",
                ('{"tampered": true}', middle["id"]),
            )
            expected_id, expected_reason = middle["id"], "row-hash-mismatch"
        elif mode == "delete":
            conn.execute("DELETE FROM audit_events WHERE id=?", (middle["id"],))
            expected_id, expected_reason = (
                rows_a[rows_a.index(middle) + 1]["id"],
                "prev-hash-mismatch",
            )
        else:
            # Reorder tamper: two adjacent rows trade their predecessor links, as a
            # writer would if it rewrote the chain order. Content stays intact, so
            # only the link check can catch it.
            following = rows_a[rows_a.index(middle) + 1]
            conn.execute(
                "UPDATE audit_events SET prev_hash=? WHERE id=?",
                (following["prev_hash"], middle["id"]),
            )
            conn.execute(
                "UPDATE audit_events SET prev_hash=? WHERE id=?",
                (middle["prev_hash"], following["id"]),
            )
            expected_id, expected_reason = middle["id"], "prev-hash-mismatch"

    breaks = verify_sqlite(c.db.path)
    assert breaks, f"{mode} tampering went undetected"
    first = breaks[0]
    assert first.firm_id == firm_a.firm_id
    assert first.row_id == expected_id
    assert first.reason == expected_reason
    # The untouched firm's chain still recomputes on its own.
    assert [b for b in breaks if b.firm_id == firm_b.firm_id] == []
