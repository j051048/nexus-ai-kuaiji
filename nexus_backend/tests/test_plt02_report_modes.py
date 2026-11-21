"""PLT02-B: trial records cannot be reviewed or published; formal fields are frozen."""

import json
import sqlite3
import uuid

import pytest

from app.accounting.domain import AccountingError
from app.opening.models import PolicyCreate
from app.opening.service import OpeningService
from app.posting.models import OpeningBind
from app.posting.service import PostingService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from policy_fixtures import approve_policy_independently
from test_business import business  # noqa: F401
from test_posting_service import frozen_opening


def _prepared(case):
    opening = frozen_opening(case)
    PostingService(case.db).bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=opening["version_id"],
            reason="PLT02-B 合成期初绑定",
        ),
    )
    with case.db.transaction() as conn:
        policy = conn.execute(
            "SELECT * FROM accounting_policies WHERE ledger_id=("
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?) AND status='active'",
            (case.book["id"],),
        ).fetchone()
    return opening, policy


def test_formal_report_records_policy_and_mapping(business):
    case = business
    _, policy = _prepared(case)
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")["report"]
    assert report["mode"] == "formal"
    assert report["standard_kind"] == "small_enterprise"
    assert report["policy_version"] == policy["version"]
    assert report["policy_approver"] == policy["approved_by"]
    assert report["policy_approver"] != case.manager.user_id
    assert report["mapping_fingerprint"] == policy["mapping_fingerprint"]


def test_synthetic_policy_is_trial_and_cannot_be_reviewed(business):
    case = business
    _, policy = _prepared(case)
    with case.db.transaction(write=True) as conn:
        conn.execute("UPDATE accounting_policies SET is_synthetic=1 WHERE id=?", (policy["id"],))
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    assert report["report"]["mode"] == "trial"
    assert "合成演练" in "".join(report["report"]["trial_reasons"])
    with pytest.raises(AccountingError, match="试算报表不能进入复核"):
        ReportService(case.db).review(
            case.actors["accountant"],
            report["report"]["id"],
            ReportCommand(expected_version=1, reason="PLT02 合成模式测试"),
        )


def test_mapping_change_after_approval_forces_trial(business):
    case = business
    _, policy = _prepared(case)
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO report_line_mappings(id,firm_id,template_id,template_version,version,"
            "line_code,line_name,line_kind,account_prefix,sign,active,created_by,created_at) "
            "VALUES(?,?,?,?,1,'revenue','营业收入','revenue','6001',1,TRUE,?,?)",
            (
                uuid.uuid4().hex,
                case.manager.firm_id,
                "small_enterprise_basic",
                1,
                case.manager.user_id,
                "2026-11-30T00:00:00+00:00",
            ),
        )
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    assert report["report"]["mode"] == "trial"
    assert "映射" in "".join(report["report"]["trial_reasons"])
    assert policy["mapping_fingerprint"] != report["report"]["mapping_fingerprint"]


def test_trial_publish_rejected_by_service_and_database(business):
    case = business
    _, policy = _prepared(case)
    with case.db.transaction(write=True) as conn:
        conn.execute("UPDATE accounting_policies SET is_synthetic=1 WHERE id=?", (policy["id"],))
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")["report"]
    with pytest.raises(AccountingError, match="试算报表不能发布") as error:
        ReportService(case.db).publish(
            case.manager,
            report["id"],
            ReportCommand(expected_version=1, reason="PLT02 合成模式测试"),
        )
    assert error.value.status == 409
    with pytest.raises(sqlite3.IntegrityError, match="试算报表不能发布"):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "UPDATE report_runs SET status='published',published_by=?,published_at=? WHERE id=?",
                (case.manager.user_id, "2026-11-30T00:00:00+00:00", report["id"]),
            )


def test_policy_list_exposes_approval_and_mapping_status(business):
    case = business
    _, policy = _prepared(case)
    opening = OpeningService(case.db)
    listed = next(
        row for row in opening.list_policies(case.manager, policy["ledger_id"]) if row["id"] == policy["id"]
    )
    assert listed["approved_by"] == policy["approved_by"]
    assert listed["approved_by"] != case.manager.user_id
    assert listed["is_synthetic"] == 0
    assert listed["mapping_matches"] is True
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "INSERT INTO report_line_mappings(id,firm_id,template_id,template_version,version,"
            "line_code,line_name,line_kind,account_prefix,sign,active,created_by,created_at) "
            "VALUES(?,?,?,?,1,'expense','营业费用','admin','6602',1,TRUE,?,?)",
            (
                uuid.uuid4().hex,
                case.manager.firm_id,
                "small_enterprise_basic",
                1,
                case.manager.user_id,
                "2026-11-30T00:00:00+00:00",
            ),
        )
    listed = next(
        row for row in opening.list_policies(case.manager, policy["ledger_id"]) if row["id"] == policy["id"]
    )
    assert listed["mapping_matches"] is False


def test_policy_without_approval_is_trial(business):
    case = business
    _, policy = _prepared(case)
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE accounting_policies SET approved_by=NULL,approved_at=NULL,mapping_fingerprint=NULL "
            "WHERE id=?",
            (policy["id"],),
        )
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    assert report["report"]["mode"] == "trial"
    assert "尚未批准" in "".join(report["report"]["trial_reasons"])


def test_policy_maker_cannot_approve_own_policy(business):
    case = business
    _, existing = _prepared(case)
    opening = OpeningService(case.db)
    policy = opening.create_policy(
        case.manager,
        PolicyCreate(
            ledger_id=existing["ledger_id"],
            standard_kind="small_enterprise",
            taxpayer_kind="general",
            formal_book_system="local",
            effective_from="2027-01",
            effective_to="2027-12",
            reason="PLT02-D 独立批准边界",
        ),
    )
    with pytest.raises(AccountingError, match="制单人与批准人必须不同") as error:
        opening.approve_policy(case.manager, policy["id"])
    assert error.value.status == 409
    approver, approved = approve_policy_independently(
        opening, case.db, case.manager, policy["id"]
    )
    assert approver.user_id != case.manager.user_id
    assert approved["approved_by"] == approver.user_id


def _migration_text(name):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "migrations" / "sqlite"
    return (root / name).read_text(encoding="utf-8")


def test_unassessed_policy_refuses_report_instead_of_small_enterprise(business):
    """RWK04-A：没有生效政策就不出表，不得静默回落到小企业模板。"""
    case = business
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE accounting_policies SET status='retired' WHERE ledger_id="
            "(SELECT ledger_id FROM ledger_periods WHERE book_id=?)",
            (case.book["id"],),
        )
        before = conn.execute("SELECT COUNT(*) FROM report_runs").fetchone()[0]
    with pytest.raises(AccountingError, match="核算政策未核定"):
        ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    with case.db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM report_runs").fetchone()[0] == before


def test_pre_gate_rows_read_as_legacy_and_cannot_publish(business):
    """RWK04-B：门槛建立前生成的存量行按 legacy 口径读取，且不可新发布。"""
    case = business
    _, policy = _prepared(case)
    created = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    report_id = created["report"]["id"]
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE report_runs SET mode='formal', policy_version=NULL, policy_approver=NULL "
            "WHERE id=?",
            (report_id,),
        )
    listed = next(
        row for row in ReportService(case.db).list(case.manager, case.book["id"]) if row["id"] == report_id
    )
    assert listed["mode"] == "legacy"
    assert "历史未核定" in listed["mode_label"]
    with case.db.transaction(write=True) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="历史未核定报表同样不可新发布"):
            conn.execute("UPDATE report_runs SET status='published' WHERE id=?", (report_id,))
