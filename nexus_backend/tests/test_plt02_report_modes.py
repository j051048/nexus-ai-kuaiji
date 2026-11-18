"""PLT02-B: trial records cannot be reviewed or published; formal fields are frozen."""

import json
import sqlite3
import uuid

import pytest

from app.accounting.domain import AccountingError
from app.posting.models import OpeningBind
from app.posting.service import PostingService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
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
    assert report["policy_approver"] == case.manager.user_id
    assert report["mapping_fingerprint"] == policy["mapping_fingerprint"]


def test_synthetic_policy_is_trial_and_cannot_be_reviewed(business):
    case = business
    _, policy = _prepared(case)
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE accounting_policies SET is_synthetic=1 WHERE id=?", (policy["id"],)
        )
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    assert report["report"]["mode"] == "trial"
    assert "合成演练" in "".join(report["report"]["trial_reasons"])
    with pytest.raises(AccountingError, match="试算报表不能进入复核"):
        ReportService(case.db).review(
            case.actors["accountant"], report["report"]["id"], ReportCommand(expected_version=1, reason="PLT02 合成模式测试")
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
        ReportService(case.db).publish(case.manager, report["id"], ReportCommand(expected_version=1, reason="PLT02 合成模式测试"))
    assert error.value.status == 409
    with pytest.raises(sqlite3.IntegrityError, match="试算报表不能发布"):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "UPDATE report_runs SET status='published',published_by=?,published_at=? WHERE id=?",
                (case.manager.user_id, "2026-11-30T00:00:00+00:00", report["id"]),
            )


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
