"""Internal month close. External delivery close remains a separate workflow."""

import hashlib
import json
from uuid import uuid4

from ..accounting.close_dependencies import close_dependencies
from ..accounting.domain import AccountingError
from ..accounting.service import AccountingService, now
from ..posting.service import PostingService
from ..reports.models import ReportCommand
from ..reports.service import ReportService
from ..subledger.service import SubledgerService
from .models import CloseCommand, ClosePreflight


def _fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class CloseService:
    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)
        self.reports = ReportService(db)
        self.subledger = SubledgerService(db)
        self.posting = PostingService(db)

    def _book(self, conn, actor, book_id, write=False):
        return self.accounting.access.book(conn, actor, book_id, "review" if write else "read")

    def _company_for_book(self, actor, book_id):
        # Discover the target with normal authorization, then authorize again
        # after entering the company's established shared-global/company gate.
        with self.db.transaction(actor=actor) as conn:
            return self._book(conn, actor, book_id, write=True)["company_id"]

    def _company_for_run(self, actor, run_id):
        with self.db.transaction(actor=actor) as conn:
            run = self._run(conn, actor, run_id, write=True)
            return self._book(conn, actor, run["book_id"], write=True)["company_id"]

    def _snapshot(self, conn, actor, book, period, report_id):
        if period != book["period"] or book["period_status"] != "open":
            raise AccountingError("月结期间与开放的账套期间不一致", 409)
        report = conn.execute(
            "SELECT * FROM report_runs WHERE id=? AND book_id=? AND period=?",
            (report_id, book["id"], period),
        ).fetchone()
        if not report:
            raise AccountingError("报表不存在或不属于当前账套/期间", 404)
        if report["status"] not in {"reviewed", "published"}:
            raise AccountingError("报表尚未复核，不能进行月结预检", 409)
        # Every input is read on this connection while the company gate is held;
        # no nested transaction can supply an older balance or reconciliation.
        trial = self.posting.trial_balance_in_transaction(conn, actor, book["id"])
        self.reports._current_in_transaction(conn, actor, report["id"], trial=trial)
        control = self.subledger.control_reconciliation_in_transaction(
            conn, actor, book["id"], trial=trial
        )
        bank = self.subledger.bank_reconciliation_in_transaction(
            conn, actor, book["id"], trial=trial
        )
        unposted = conn.execute(
            "SELECT COUNT(*) FROM periodic_drafts WHERE book_id=? AND period=? AND status<>'posted'",
            (book["id"], period),
        ).fetchone()[0]
        unapproved = conn.execute(
            "SELECT COUNT(*) FROM drafts WHERE book_id=? AND status NOT IN('approved','exported')",
            (book["id"],),
        ).fetchone()[0]
        report_content = json.loads(report["content_json"])
        checks = [
            {
                "code": "unposted_periodic",
                "status": "blocked" if unposted else "pass",
                "detail": f"{unposted} 张期末草稿未记账",
            },
            {
                "code": "unapproved_drafts",
                "status": "blocked" if unapproved else "pass",
                "detail": f"{unapproved} 张草稿未审核/导出",
            },
            {
                "code": "subledger_control",
                "status": "pass" if control["passed"] else "blocked",
                "detail": control["rows"],
            },
            {
                "code": "bank_reconciliation",
                "status": "pass" if bank["passed"] else "blocked",
                "detail": bank,
            },
            {
                "code": "trial_balance",
                "status": "pass" if int(trial["totals"]["closing_cents"]) == 0 else "blocked",
                "detail": trial["totals"],
            },
            {
                "code": "report_checks",
                "status": "pass"
                if all(
                    report_content["checks"].get(key)
                    for key in (
                        "balance_sheet_passed",
                        "profit_statement_passed",
                        "cash_flow_passed",
                    )
                )
                and report_content["checks"].get("unclassified_cash_cents") == 0
                else "blocked",
                "detail": report_content["checks"],
            },
        ]
        dependencies = close_dependencies(conn, book["id"], report["template_id"])
        fingerprint = _fingerprint({
            "dependencies": dependencies,
            "report": {
                "id": report["id"], "version": int(report["version"]),
                "inputs_fingerprint": report["inputs_fingerprint"],
                "content_hash": report["content_hash"],
                "template_version": int(report["template_version"]),
            },
            "checks": checks,
            "trial": trial,
        })
        return checks, dependencies, fingerprint

    def _assert_current(self, conn, actor, run):
        book = self._book(conn, actor, run["book_id"], write=True)
        try:
            checks, dependencies, fingerprint = self._snapshot(
                conn, actor, book, run["period"], run["report_run_id"]
            )
        except AccountingError as error:
            if error.status != 409:
                raise
            raise AccountingError("月结预检已变化，请重新预检并复核", 409) from error
        if (
            json.loads(run["dependencies_json"]) != dependencies
            or run["inputs_fingerprint"] != fingerprint
            or any(check["status"] == "blocked" for check in checks)
        ):
            raise AccountingError("月结预检已变化，请重新预检并复核", 409)
        return book

    def preflight(self, actor, request: ClosePreflight):
        company_id = self._company_for_book(actor, request.book_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            book = self._book(conn, actor, request.book_id, write=True)
            if request.period != book["period"]:
                raise AccountingError("月结期间与账套期间不一致", 422)
            if conn.execute(
                "SELECT 1 FROM internal_period_locks WHERE book_id=? AND period=?",
                (book["id"], request.period),
            ).fetchone():
                raise AccountingError("该期间已经内部锁期", 409)
            checks, dependencies, fingerprint = self._snapshot(
                conn, actor, book, request.period, request.report_id
            )
            status = (
                "preflight_failed"
                if any(check["status"] == "blocked" for check in checks)
                else "preflight_passed"
            )
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM internal_close_runs WHERE book_id=? AND period=?",
                (book["id"], request.period),
            ).fetchone()[0]
            run_id = uuid4().hex
            conn.execute(
                "INSERT INTO internal_close_runs(id,book_id,period,status,version,inputs_fingerprint,"
                "checks_json,dependencies_json,report_run_id,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id, book["id"], request.period, status, version, fingerprint,
                    json.dumps(checks, ensure_ascii=False),
                    json.dumps(dependencies, ensure_ascii=False, sort_keys=True),
                    request.report_id, actor.user_id, now(),
                ),
            )
            self._audit(conn, actor, book["id"], "close.preflight", run_id, {"status": status})
            return self._view(conn, run_id)

    def review(self, actor, run_id, request: CloseCommand):
        company_id = self._company_for_run(actor, run_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            run = self._run(conn, actor, run_id, write=True)
            if run["status"] != "preflight_passed":
                raise AccountingError("只有预检通过的月结才能复核", 409)
            if int(run["version"]) != request.expected_version:
                raise AccountingError("月结版本已变化", 409)
            if run["created_by"] == actor.user_id:
                raise AccountingError("月结预检人与复核人必须分离", 403)
            self._assert_current(conn, actor, run)
            conn.execute(
                "UPDATE internal_close_runs SET status='reviewed',reviewed_by=?,reviewed_at=? WHERE id=?",
                (actor.user_id, now(), run_id),
            )
            self._audit(conn, actor, run["book_id"], "close.review", run_id, {})
            return self._view(conn, run_id)

    def lock(self, actor, run_id, request: CloseCommand):
        company_id = self._company_for_run(actor, run_id)
        with self.db.transaction(
            write=True, actor=actor, company_id=company_id, write_scope="company"
        ) as conn:
            run = self._run(conn, actor, run_id, write=True)
            if run["status"] != "reviewed":
                raise AccountingError("只有已复核的月结才能锁期", 409)
            if int(run["version"]) != request.expected_version:
                raise AccountingError("月结版本已变化", 409)
            if conn.execute(
                "SELECT 1 FROM internal_period_locks WHERE book_id=? AND period=?",
                (run["book_id"], run["period"]),
            ).fetchone():
                raise AccountingError("该期间已经内部锁期", 409)
            book = self._assert_current(conn, actor, run)
            ledger = conn.execute(
                "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
            ).fetchone()
            lock_id = uuid4().hex
            conn.execute(
                "INSERT INTO internal_period_locks(id,firm_id,company_id,ledger_id,book_id,period,"
                "close_run_id,locked_by,locked_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    lock_id, book["firm_id"], book["company_id"], ledger["ledger_id"],
                    run["book_id"], run["period"], run_id, actor.user_id, now(),
                ),
            )
            conn.execute(
                "UPDATE internal_close_runs SET status='locked',locked_by=?,locked_at=? WHERE id=?",
                (actor.user_id, now(), run_id),
            )
            self._audit(conn, actor, run["book_id"], "close.lock", run_id, {})
            return self._view(conn, run_id)

    def publish(self, actor, run_id, request: CloseCommand):
        with self.db.transaction(write=True, actor=actor) as conn:
            run = self._run(conn, actor, run_id, write=True)
            self.accounting.access.book(conn, actor, run["book_id"], "review")
            if run["status"] != "locked":
                raise AccountingError("只有已锁期的月结才能发布", 409)
            if int(run["version"]) != request.expected_version:
                raise AccountingError("月结版本已变化", 409)
            report_id = run["report_run_id"]
            report = conn.execute(
                "SELECT version FROM report_runs WHERE id=?", (report_id,)
            ).fetchone()
        self.reports.publish(
            actor,
            report_id,
            ReportCommand(expected_version=report["version"], reason=request.reason),
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            self.accounting.access.book(conn, actor, run["book_id"], "review")
            changed = conn.execute(
                "UPDATE internal_close_runs SET status='published',published_by=?,published_at=? "
                "WHERE id=? AND status='locked'",
                (actor.user_id, now(), run_id),
            )
            if changed.rowcount != 1:
                raise AccountingError("月结状态已变化，未发布", 409)
            self._audit(
                conn, actor, run["book_id"], "close.publish", run_id, {"report_id": report_id}
            )
            return self._view(conn, run_id)

    def reopen(self, actor, run_id, request: CloseCommand):
        raise AccountingError("重开策略尚未批准，本批明确禁止重开已锁期", 409)

    def get(self, actor, run_id):
        with self.db.transaction(actor=actor) as conn:
            row = self._run(conn, actor, run_id)
            return {"close": dict(row), "checks": json.loads(row["checks_json"])}

    def list(self, actor, book_id):
        with self.db.transaction(actor=actor) as conn:
            book = self._book(conn, actor, book_id)
            rows = conn.execute(
                "SELECT * FROM internal_close_runs WHERE book_id=? ORDER BY period DESC,version DESC",
                (book["id"],),
            ).fetchall()
            return [dict(row) for row in rows]

    def _run(self, conn, actor, run_id, write=False):
        row = conn.execute("SELECT * FROM internal_close_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            raise AccountingError("月结记录不存在", 404)
        self._book(conn, actor, row["book_id"], write=write)
        return row

    def _view(self, conn, run_id):
        row = conn.execute("SELECT * FROM internal_close_runs WHERE id=?", (run_id,)).fetchone()
        return {"close": dict(row), "checks": json.loads(row["checks_json"])}

    @staticmethod
    def _audit(conn, actor, book_id, action, object_id, details):
        conn.execute(
            "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                actor.firm_id,
                book_id,
                actor.user_id,
                action,
                object_id,
                json.dumps(details, ensure_ascii=False),
                now(),
            ),
        )
