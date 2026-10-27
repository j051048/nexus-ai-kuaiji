"""Native SQL must retain the same responsibility boundary as service review."""

import json
import sqlite3
from hashlib import sha256
from uuid import uuid4

import pytest

from app.accounting.models import ImportRequest, SourceRow
from test_business import business  # noqa: F401


@pytest.fixture
def responsibility_case(business):
    c = business
    c.ledger.import_sources(
        c.manager,
        c.book["id"],
        ImportRequest(
            source_name="独立复核合成流水",
            rows=[
                SourceRow(
                    external_id="rr-001",
                    date="2026-10-03",
                    description="办公用品",
                    counterparty="合成供应商",
                    amount="0.30",
                    direction="expense",
                    evidence_ref="合成原始凭据",
                )
            ],
        ),
    )
    c.ledger.suggestions(c.manager, c.book["id"])
    c.draft = c.ledger.get_book(c.manager, c.book["id"])["drafts"][0]
    with c.db.transaction(actor=c.manager) as conn:
        c.ledger_id = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (c.book["id"],)
        ).fetchone()[0]
    return c


def approve_sql(c, actor, *, policy=0, reason="独立逐笔核对凭证"):
    with c.db.transaction(write=True, actor=actor) as conn:
        conn.execute(
            "UPDATE drafts SET status='approved',version=version+1,approved_by=?,approved_at='2026-10-25T00:00:00Z',review_policy_version=?,review_reason=? WHERE id=?",
            (actor.user_id, policy, reason, c.draft["id"]),
        )


def policy_sql(c, actor, *, version=1, mode="single_operator", reason="本地显式单人经营政策"):
    with c.db.transaction(write=True, actor=actor) as conn:
        conn.execute(
            "INSERT INTO ledger_review_policies(ledger_id,version,firm_id,company_id,mode,reason,confirmed_by,created_at) VALUES(?,?,?,?,?,?,?,'2026-10-25T00:00:00Z')",
            (c.ledger_id, version, c.manager.firm_id, c.company["id"], mode, reason, actor.user_id),
        )


def preparation(c):
    with c.db.transaction(actor=c.manager) as conn:
        return dict(
            conn.execute(
                "SELECT * FROM draft_preparations WHERE draft_id=?", (c.draft["id"],)
            ).fetchone()
        )


def test_native_default_independent_rejects_same_actor_even_without_service(responsibility_case):
    c = responsibility_case
    # Deliberately omit new optional request fields: version 0 still enforces independence.
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "UPDATE drafts SET status='approved',version=version+1,approved_by=?,approved_at='now' WHERE id=?",
                (c.manager.user_id, c.draft["id"]),
            )


def test_preparation_uses_trusted_actor_and_another_reviewer_gets_frozen_receipt(
    responsibility_case,
):
    c = responsibility_case
    initial = preparation(c)
    assert initial["maker_user_id"] == c.manager.user_id
    assert initial["version"] == c.draft["version"] and initial["content_revision"] > 0
    approve_sql(c, c.actors["accountant"])
    with c.db.transaction(actor=c.manager) as conn:
        receipt = dict(
            conn.execute(
                "SELECT * FROM draft_review_receipts WHERE draft_id=?", (c.draft["id"],)
            ).fetchone()
        )
    assert receipt["reviewed_version"] == c.draft["version"]
    assert receipt["content_revision"] == initial["content_revision"]
    assert receipt["maker_user_id"] == c.manager.user_id
    assert receipt["reviewer_user_id"] == c.actors["accountant"].user_id
    assert receipt["mode"] == "independent" and receipt["independent"] == 1
    assert receipt["policy_version"] == 0 and len(receipt["content_fingerprint"]) == 64


def test_native_line_change_updates_responsibility_without_version_bypass(responsibility_case):
    c = responsibility_case
    old = preparation(c)
    reviewer = c.actors["accountant"]
    with c.db.transaction(write=True, actor=reviewer) as conn:
        conn.execute(
            "UPDATE draft_lines SET debit_cents=CASE WHEN debit_cents>0 THEN 31 ELSE 0 END,credit_cents=CASE WHEN credit_cents>0 THEN 31 ELSE 0 END WHERE draft_id=?",
            (c.draft["id"],),
        )
    changed = preparation(c)
    assert changed["maker_user_id"] == reviewer.user_id
    assert changed["content_revision"] > old["content_revision"]
    with pytest.raises(sqlite3.IntegrityError):
        approve_sql(c, reviewer)


def test_initial_insert_keeps_one_logical_pending_version(responsibility_case):
    assert responsibility_case.draft["version"] == 1


@pytest.mark.parametrize("header", ["explicit", "noop", "none"])
def test_native_financial_writes_advance_once_per_transaction(responsibility_case, header):
    c = responsibility_case
    before = preparation(c)
    with c.db.transaction(write=True, actor=c.actors["accountant"]) as conn:
        if header == "explicit":
            conn.execute("UPDATE drafts SET version=version+1 WHERE id=?", (c.draft["id"],))
        elif header == "noop":
            conn.execute("UPDATE drafts SET version=version WHERE id=?", (c.draft["id"],))
        conn.execute(
            "UPDATE draft_lines SET debit_cents=CASE WHEN debit_cents>0 THEN 31 ELSE 0 END,credit_cents=CASE WHEN credit_cents>0 THEN 31 ELSE 0 END WHERE draft_id=?",
            (c.draft["id"],),
        )
    after = preparation(c)
    assert after["version"] == before["version"] + 1
    assert after["content_revision"] > before["content_revision"]
    assert after["maker_user_id"] == c.actors["accountant"].user_id


def test_service_edit_preserves_legacy_command_expected_plus_one(responsibility_case):
    from app.accounting.models import DraftUpdate

    c = responsibility_case
    before = c.ledger.get_draft(c.manager, c.book["id"], c.draft["id"])
    edited = c.ledger.update_draft(
        c.actors["accountant"],
        c.book["id"],
        c.draft["id"],
        DraftUpdate(
            expected_version=before["version"],
            description="合成完整逻辑编辑",
            lines=before["lines"],
            correction_reason="保持一次命令推进一个版本",
        ),
    )
    assert edited["version"] == before["version"] + 1


def test_pending_draft_write_without_trusted_transaction_is_rejected(responsibility_case):
    c = responsibility_case
    conn = c.db.connect()
    try:
        conn.isolation_level = None
        assert conn.execute("SELECT accounting_transaction_id()").fetchone()[0] == ""
        with pytest.raises(sqlite3.IntegrityError, match="Explicit trusted review transaction"):
            conn.execute("UPDATE drafts SET version=version+1 WHERE id=?", (c.draft["id"],))
    finally:
        conn.close()


def test_version_only_change_does_not_reassign_maker(responsibility_case):
    c = responsibility_case
    old = preparation(c)
    with c.db.transaction(write=True, actor=c.actors["accountant"]) as conn:
        conn.execute("UPDATE drafts SET version=version+1 WHERE id=?", (c.draft["id"],))
    new = preparation(c)
    assert new["maker_user_id"] == old["maker_user_id"]
    assert new["version"] == old["version"] + 1


def test_current_policy_version_and_single_operator_flag_are_enforced(responsibility_case):
    c = responsibility_case
    policy_sql(c, c.manager)
    with pytest.raises(sqlite3.IntegrityError):
        approve_sql(c, c.manager, policy=0)
    approve_sql(c, c.manager, policy=1)
    with c.db.transaction(actor=c.manager) as conn:
        row = conn.execute(
            "SELECT mode,independent,policy_version FROM draft_review_receipts WHERE draft_id=?",
            (c.draft["id"],),
        ).fetchone()
        assert tuple(row) == ("single_operator", 0, 1)


def test_policy_requires_manager_sequential_version_reason_and_is_immutable(responsibility_case):
    c = responsibility_case
    for actor, version, reason in (
        (c.actors["accountant"], 1, "当前非管理员不可降级"),
        (c.manager, 2, "版本必须连续"),
        (c.manager, 1, ""),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            policy_sql(c, actor, version=version, reason=reason)
    policy_sql(c, c.manager)
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "UPDATE ledger_review_policies SET mode='independent' WHERE ledger_id=?",
                (c.ledger_id,),
            )


def test_same_user_cannot_forge_other_preparer_or_receipt(responsibility_case):
    c = responsibility_case
    for statement, values in (
        (
            "UPDATE draft_preparations SET maker_user_id=? WHERE draft_id=?",
            (c.actors["accountant"].user_id, c.draft["id"]),
        ),
        ("DELETE FROM draft_preparation_history WHERE draft_id=?", (c.draft["id"],)),
    ):
        with pytest.raises(sqlite3.DatabaseError):
            with c.db.transaction(write=True, actor=c.manager) as conn:
                conn.execute(statement, values)
    assert preparation(c)["maker_user_id"] == c.manager.user_id


def test_approved_content_and_receipt_are_immutable(responsibility_case):
    c = responsibility_case
    approve_sql(c, c.actors["accountant"])
    for statement in (
        "UPDATE draft_lines SET debit_cents=debit_cents+1 WHERE draft_id=? AND debit_cents>0",
        "DELETE FROM draft_review_receipts WHERE draft_id=?",
    ):
        with pytest.raises(sqlite3.DatabaseError):
            with c.db.transaction(write=True, actor=c.manager) as conn:
                conn.execute(statement, (c.draft["id"],))


def test_claim_cannot_overwrite_current_known_preparer(responsibility_case):
    c = responsibility_case
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True, actor=c.actors["accountant"]) as conn:
            conn.execute(
                "UPDATE drafts SET preparation_claim_reason='不能覆盖现有真实制单责任' WHERE id=?",
                (c.draft["id"],),
            )


def manual_case(c):
    from app.posting.manual_review import ManualReviewService
    from app.accounting.review_policy_models import ManualReviewApproval, ManualReviewPost
    from test_review_policy_service import manual

    _, payload = manual(c)
    service = ManualReviewService(c.db)
    pending = service.prepare(c.manager, payload)["request"]
    approved = service.approve(
        c.actors["accountant"],
        pending["id"],
        ManualReviewApproval(
            expected_version=1,
            expected_policy_version=0,
            reason="逐项独立核对合成数据",
        ),
    )["request"]
    posted = service.post(
        c.manager,
        approved["id"],
        ManualReviewPost(
            expected_version=approved["version"],
            command_key=payload.command_key,
        ),
    )
    with c.db.transaction(actor=c.manager) as conn:
        voucher = dict(
            conn.execute("SELECT * FROM vouchers WHERE id=?", (posted["voucher"]["id"],)).fetchone()
        )
        lines = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM voucher_lines WHERE voucher_id=? ORDER BY line_no", (voucher["id"],)
            )
        ]
        command = dict(
            conn.execute(
                "SELECT * FROM posting_commands WHERE id=?", (voucher["command_id"],)
            ).fetchone()
        )
    new_payload = payload.model_copy(update={"command_key": "native-review-" + uuid4().hex})
    new_pending = service.prepare(c.manager, new_payload)["request"]
    new_approved = service.approve(
        c.actors["accountant"],
        new_pending["id"],
        ManualReviewApproval(
            expected_version=1,
            expected_policy_version=0,
            reason="再次独立核对合成数据",
        ),
    )["request"]
    voucher.update(
        id=uuid4().hex,
        command_id=uuid4().hex,
        voucher_no=voucher["voucher_no"] + 1,
        review_request_id=new_approved["id"],
    )
    voucher["review_completion_id"] = voucher["id"]
    command.update(
        id=voucher["command_id"], voucher_id=voucher["id"], command_key=new_payload.command_key
    )
    for line in lines:
        line.update(id=uuid4().hex, voucher_id=voucher["id"])
    return voucher, lines, command


def insert_row(conn, table, values):
    conn.execute(
        "INSERT INTO "
        + table
        + "("
        + ",".join(values)
        + ") VALUES("
        + ",".join("?" for _ in values)
        + ")",
        tuple(values.values()),
    )


@pytest.mark.parametrize(
    "mutation", ["line_amount", "header", "reviewer", "missing_line", "snapshot"]
)
def test_native_voucher_completion_rejects_forged_frozen_content(business, mutation):
    c = business
    voucher, lines, command = manual_case(c)
    if mutation == "line_amount":
        for line in lines:
            if line["debit_cents"]:
                line["debit_cents"] += 1
            if line["credit_cents"]:
                line["credit_cents"] += 1
    elif mutation == "header":
        voucher["description"] = "审核后伪造摘要"
    elif mutation == "reviewer":
        voucher["review_reviewer_id"] = c.manager.user_id
    elif mutation == "missing_line":
        lines.pop()
    elif mutation == "snapshot":
        lines[0]["name_snapshot"] = "伪造科目名称"
    with pytest.raises(sqlite3.DatabaseError):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            insert_row(conn, "vouchers", voucher)
            for line in lines:
                insert_row(conn, "voucher_lines", line)
            insert_row(conn, "posting_commands", command)
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM vouchers WHERE id=?", (voucher["id"],)).fetchone()[0]
            == 0
        )


def test_native_voucher_cannot_commit_without_validated_completion(business):
    c = business
    voucher, lines, _ = manual_case(c)
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            insert_row(conn, "vouchers", voucher)
            for line in lines:
                insert_row(conn, "voucher_lines", line)


def test_native_manual_request_cannot_be_approved_with_incomplete_payload(business):
    c = business
    content = json.dumps(
        {"book_id": c.book["id"], "command_key": "native-incomplete", "lines": [{}, {}]}
    )
    with pytest.raises(sqlite3.DatabaseError):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "INSERT INTO manual_review_requests(id,book_id,version,prepared_by,status,policy_version,content_json,content_fingerprint,command_key,reason,created_at) VALUES(?,?,1,?,'pending',0,?,?,?,'合成不完整内容负向','now')",
                (
                    uuid4().hex,
                    c.book["id"],
                    c.manager.user_id,
                    content,
                    sha256(content.encode()).hexdigest(),
                    "native-incomplete",
                ),
            )


def periodic_pending(c):
    from app.periodic.models import AccrualRule
    from app.periodic.service import PeriodicService
    from test_b04_pilot import setup_b03

    opening, _ = setup_b03(c)
    service = PeriodicService(c.db)
    rule = service.create_accrual_rule(
        c.manager,
        AccrualRule(
            book_id=c.book["id"],
            name="合成原生计提",
            amount="0.30",
            debit_account_id=opening["expense"]["id"],
            debit_account_version=1,
            credit_account_id=opening["payable"]["id"],
            credit_account_version=1,
            reason="合成计提独立复核验收",
        ),
    )
    return service, service.generate_accrual(c.manager, rule["id"])["draft"]


def test_periodic_reviewer_cannot_change_prepared_lines_then_self_review(business):
    c = business
    _, draft = periodic_pending(c)
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True, actor=c.actors["accountant"]) as conn:
            conn.execute(
                "UPDATE periodic_draft_lines SET source_ref='他人未复核改写' WHERE draft_id=?",
                (draft["id"],),
            )


def test_periodic_content_change_invalidates_previously_seen_version(business):
    from app.accounting.domain import AccountingError
    from app.periodic.models import DraftCommand

    c = business
    service, draft = periodic_pending(c)
    with c.db.transaction(write=True, actor=c.manager) as conn:
        conn.execute(
            "UPDATE periodic_draft_lines SET source_ref='原制单人实际改写' WHERE draft_id=?",
            (draft["id"],),
        )
    with pytest.raises(AccountingError):
        service.approve(
            c.actors["accountant"],
            draft["id"],
            DraftCommand(expected_version=draft["version"], reason="旧页面审核版本已失效"),
        )


def test_review_policy_barrier_rejects_previous_sqlite_runtime(tmp_path, monkeypatch):
    from app.accounting import database as module

    path = tmp_path / "review-compatibility.db"
    module.Database(path).close()
    available = module.migration_files
    monkeypatch.setattr(
        module,
        "migration_files",
        lambda dialect: [p for p in available(dialect) if int(p.name[:3]) <= 100],
    )
    with pytest.raises(RuntimeError, match="未知的迁移版本"):
        module.Database(path).close()


def test_review_policy_postgres_boundary_is_explicitly_breaking():
    from app.accounting.database import MIGRATIONS, migration_compatibility

    assert (
        migration_compatibility(MIGRATIONS / "postgres" / "163_review_workflow_completion.sql")
        == "breaking"
    )


def prepared_manual(c):
    from app.posting.manual_review import ManualReviewService
    from test_review_policy_service import manual

    _, request = manual(c)
    return ManualReviewService(c.db).prepare(c.manager, request)["request"]


def withdraw_sql(c, actor, request):
    with c.db.transaction(write=True, actor=actor) as conn:
        conn.execute(
            "INSERT INTO manual_review_withdrawals(request_id,book_id,request_version,reason,withdrawn_by,created_at) VALUES(?,?,?,'原制单人明确撤回合成申请',?,'now')",
            (request["id"], c.book["id"], request["version"], actor.user_id),
        )


def test_manual_withdrawal_binds_preparer_and_blocks_later_approval(business):
    c = business
    request = prepared_manual(c)
    with pytest.raises(sqlite3.DatabaseError):
        withdraw_sql(c, c.actors["accountant"], request)
    withdraw_sql(c, c.manager, request)
    with pytest.raises(sqlite3.IntegrityError):
        with c.db.transaction(write=True, actor=c.actors["accountant"]) as conn:
            conn.execute(
                "UPDATE manual_review_requests SET status='approved',version=version+1,reviewed_by=?,reviewed_at='now',independent=1,review_reason='已撤回申请不得审核' WHERE id=?",
                (c.actors["accountant"].user_id, request["id"]),
            )
    with pytest.raises(sqlite3.DatabaseError):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute(
                "DELETE FROM manual_review_withdrawals WHERE request_id=?", (request["id"],)
            )


@pytest.mark.parametrize("approved", [False, True])
def test_native_period_close_rejects_unfinished_manual_review(business, approved):
    from app.posting.manual_review import ManualReviewService
    from app.accounting.review_policy_models import ManualReviewApproval

    c = business
    request = prepared_manual(c)
    if approved:
        ManualReviewService(c.db).approve(
            c.actors["accountant"],
            request["id"],
            ManualReviewApproval(
                expected_version=1, expected_policy_version=0, reason="合成凭证已审但未记账"
            ),
        )
    with pytest.raises(sqlite3.IntegrityError, match="Outstanding manual reviews"):
        with c.db.transaction(write=True, actor=c.manager) as conn:
            conn.execute("UPDATE periods SET status='closed' WHERE book_id=?", (c.book["id"],))


def test_closing_projection_contains_manual_request_and_withdrawal_facts(business):
    c = business
    request = prepared_manual(c)
    with c.db.transaction(actor=c.manager) as conn:
        before = conn.execute(
            "SELECT inputs_json FROM closing_fact_basis WHERE book_id=?", (c.book["id"],)
        ).fetchone()[0]
        assert any(
            row["id"] == request["id"] for row in json.loads(before)["manual_review_requests"]
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM closing_issue_rows WHERE book_id=? AND category='manual_review_pending'",
                (c.book["id"],),
            ).fetchone()[0]
            == 1
        )
    withdraw_sql(c, c.manager, request)
    with c.db.transaction(actor=c.manager) as conn:
        after = conn.execute(
            "SELECT inputs_json FROM closing_fact_basis WHERE book_id=?", (c.book["id"],)
        ).fetchone()[0]
        assert after != before
        assert json.loads(after)["manual_review_withdrawals"][0]["request_id"] == request["id"]
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM closing_issue_rows WHERE book_id=? AND category='manual_review_pending'",
                (c.book["id"],),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.parametrize(
    "corruption",
    ["missing_original", "duplicate_original", "missing_manual", "false_manual_status"],
)
def test_close_preflight_preserves_original_checks_and_manual_truth(
    business, monkeypatch, corruption
):
    from app.b04_close.service import CloseService
    from test_b04_pilot import test_periodic_accrual_asset_closing_reports_and_close

    original = CloseService.preflight
    verified = []

    def check_then_preflight(service, actor, request):
        if not verified:
            with service.db.transaction(write=True, actor=actor) as conn:
                book = service._book(conn, actor, request.book_id, write=True)
                checks, dependencies, fingerprint = service._snapshot(
                    conn, actor, book, request.period, request.report_id
                )
                if corruption == "missing_original":
                    checks = [item for item in checks if item["code"] != "trial_balance"]
                elif corruption == "duplicate_original":
                    checks = [
                        item if item["code"] != "trial_balance" else checks[0] for item in checks
                    ]
                elif corruption == "missing_manual":
                    checks = [item for item in checks if item["code"] != "unposted_manual"]
                else:
                    checks = [
                        dict(item, status="blocked") if item["code"] == "unposted_manual" else item
                        for item in checks
                    ]
                status = (
                    "preflight_failed"
                    if any(item["status"] == "blocked" for item in checks)
                    else "preflight_passed"
                )
                with pytest.raises(
                    sqlite3.IntegrityError,
                    match="Close preflight checks must be complete and consistent",
                ):
                    conn.execute(
                        "INSERT INTO internal_close_runs(id,book_id,period,status,version,inputs_fingerprint,checks_json,dependencies_json,report_run_id,created_by,created_at) VALUES(?,?,?,?,1,?,?,?,?,?,'now')",
                        (
                            uuid4().hex,
                            request.book_id,
                            request.period,
                            status,
                            fingerprint,
                            json.dumps(checks),
                            json.dumps(dependencies),
                            request.report_id,
                            actor.user_id,
                        ),
                    )
            verified.append(corruption)
        return original(service, actor, request)

    monkeypatch.setattr(CloseService, "preflight", check_then_preflight)
    test_periodic_accrual_asset_closing_reports_and_close(business)
    assert verified == [corruption]


def recurring_setup(c):
    from app.monthly.models import PlanApproval
    from app.monthly.service import MonthlyService
    from test_stage17_monthly import plan

    monthly = MonthlyService(c.db, c.ledger)
    created = monthly.create_plan(c.manager, c.book["id"], plan())
    monthly.approve_plan(
        c.actors["accountant"],
        c.book["id"],
        created["id"],
        PlanApproval(version=created["version"], reason="合成月度计划独立核对"),
    )
    return monthly, created


def test_recurring_generation_binds_actual_initial_preparation_and_can_be_reviewed(business):
    from app.accounting.models import ApprovalRequest, ExportRequest
    from app.monthly.models import PlanGenerate

    c = business
    monthly, plan = recurring_setup(c)
    generated = monthly.generate(
        c.manager,
        c.book["id"],
        plan["id"],
        PlanGenerate(version=plan["version"], idempotency_key=uuid4().hex),
    )
    draft = c.ledger.get_draft(c.manager, c.book["id"], generated["draft_id"])
    assert draft["status"] == "draft" and draft["version"] == 1
    assert draft["review_responsibility"]["maker_user_id"] == c.manager.user_id
    with c.db.transaction(actor=c.manager) as conn:
        preparation = conn.execute(
            "SELECT * FROM draft_preparations WHERE draft_id=?", (draft["id"],)
        ).fetchone()
        assert preparation["version"] == draft["version"]
    c.ledger.approve(
        c.actors["accountant"],
        c.book["id"],
        draft["id"],
        ApprovalRequest(expected_version=draft["version"]),
    )
    assert (
        c.ledger.export(
            c.manager,
            c.book["id"],
            ExportRequest(draft_ids=[draft["id"]], idempotency_key=uuid4().hex),
        )["count"]
        == 1
    )


@pytest.mark.parametrize(
    "change", ["extra_version", "rewritten_line", "review_metadata", "wrong_amount"]
)
def test_recurring_generation_cannot_bind_modified_pending_preparation(
    business, monkeypatch, change
):
    from app.monthly.models import PlanGenerate

    c = business
    monthly, plan = recurring_setup(c)
    original = monthly.catalog.save_auxiliaries_in_transaction

    def mutate_before_binding(conn, actor, book_id, draft_id, auxiliaries):
        original(conn, actor, book_id, draft_id, auxiliaries)
        if change == "extra_version":
            conn.execute("UPDATE drafts SET version=version+1 WHERE id=?", (draft_id,))
        elif change == "rewritten_line":
            conn.execute(
                "UPDATE draft_lines SET debit_cents=debit_cents+1 WHERE draft_id=? AND debit_cents>0",
                (draft_id,),
            )
            conn.execute(
                "UPDATE draft_lines SET debit_cents=debit_cents-1 WHERE draft_id=? AND debit_cents>0",
                (draft_id,),
            )
        elif change == "review_metadata":
            conn.execute(
                "UPDATE drafts SET review_reason='不应冒充新生成原件' WHERE id=?", (draft_id,)
            )
        else:
            conn.execute(
                "UPDATE draft_lines SET debit_cents=CASE WHEN debit_cents>0 THEN 31 ELSE 0 END,credit_cents=CASE WHEN credit_cents>0 THEN 31 ELSE 0 END WHERE draft_id=?",
                (draft_id,),
            )

    monkeypatch.setattr(monthly.catalog, "save_auxiliaries_in_transaction", mutate_before_binding)
    with pytest.raises(sqlite3.IntegrityError):
        monthly.generate(
            c.manager,
            c.book["id"],
            plan["id"],
            PlanGenerate(version=plan["version"], idempotency_key=uuid4().hex),
        )
    with c.db.transaction(actor=c.manager) as conn:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM recurring_generations WHERE plan_id=?", (plan["id"],)
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM sources WHERE book_id=?", (c.book["id"],)
            ).fetchone()[0]
            == 0
        )
