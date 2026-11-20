"""PLT02 / RWK04: no active policy must never fall back to small_enterprise; legacy reports stay isolated."""

import sqlite3
from uuid import uuid4

import pytest

from app.accounting.domain import AccountingError
from app.posting.models import OpeningBind
from app.posting.service import PostingService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from test_business import business  # noqa: F401
from test_posting_service import frozen_opening


def _prepared_case(case):
    opening = frozen_opening(case)
    PostingService(case.db).bind_opening(
        case.manager,
        OpeningBind(
            book_id=case.book["id"],
            package_version_id=opening["version_id"],
            reason="PLT02 合成期初绑定",
        ),
    )
    return opening


def test_report_without_active_policy_is_rejected_without_fallback(business):
    case = business
    _prepared_case(case)
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE accounting_policies SET status='draft' "
            "WHERE ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?)",
            (case.book["id"],),
        )
    with pytest.raises(AccountingError, match="核算政策未核定") as exc:
        ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    assert exc.value.status == 409
    with case.db.transaction() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM report_runs WHERE book_id=?", (case.book["id"],)
        ).fetchone()[0]
    assert count == 0


def test_report_with_missing_template_is_rejected_without_fallback(business):
    case = business
    _prepared_case(case)
    reports = ReportService(case.db)
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE accounting_policies SET standard_kind='enterprise' "
            "WHERE ledger_id=(SELECT ledger_id FROM ledger_periods WHERE book_id=?)",
            (case.book["id"],),
        )
        # 确保 enterprise_general 已入表后将其停用，不得静默回落小企业模板或私自重开
        reports._template_for_standard(conn, "enterprise")
        conn.execute("UPDATE report_templates SET active=0 WHERE id='enterprise_general'")
    with pytest.raises(AccountingError, match="报表模板未启用") as exc:
        reports.create(case.manager, case.book["id"], "2026-10")
    assert exc.value.status == 409
    with case.db.transaction() as conn:
        with pytest.raises(AccountingError, match="报表模板未配置") as exc2:
            reports._template_for_standard(conn, "unknown_standard")
        assert exc2.value.status == 409
        count = conn.execute(
            "SELECT COUNT(*) FROM report_runs WHERE book_id=?", (case.book["id"],)
        ).fetchone()[0]
    assert count == 0


def test_legacy_report_cannot_be_newly_published_and_inserting_unpolicied_formal_is_blocked(business):
    case = business
    _prepared_case(case)
    reports = ReportService(case.db)
    created = reports.create(case.manager, case.book["id"], "2026-10")["report"]
    # 模拟门槛建立前已存在且缺少政策字段的历史行
    with case.db.transaction(write=True) as conn:
        conn.execute(
            "UPDATE report_runs SET policy_version=NULL, policy_approver=NULL WHERE id=?",
            (created["id"],),
        )
    listed = reports.list(case.manager, case.book["id"])[0]
    assert listed["mode"] == "legacy"
    assert "历史未核定" in listed["mode_label"]
    with pytest.raises(AccountingError, match="历史未核定报表不能发布"):
        reports.publish(
            case.manager,
            created["id"],
            ReportCommand(expected_version=created["version"], reason="历史行不可新发布"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        with case.db.transaction(write=True) as conn:
            conn.execute(
                "INSERT INTO report_runs(id,book_id,period,template_id,template_version,status,"
                "version,inputs_fingerprint,content_json,content_hash,created_by,created_at,mode) "
                "VALUES(?,?,?,'small_enterprise_basic',1,'draft',99,'a','{}','b',?,'2026-10-31','formal')",
                (uuid4().hex, case.book["id"], "2026-10", case.manager.user_id),
            )


def test_report_with_active_policy_still_uses_its_standard(business):
    case = business
    _prepared_case(case)
    report = ReportService(case.db).create(case.manager, case.book["id"], "2026-10")
    assert report["report"]["template_id"] == "small_enterprise_basic"
    assert report["report"]["mode"] == "formal"
