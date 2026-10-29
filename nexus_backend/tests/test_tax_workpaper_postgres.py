"""N02: real restricted-role PostgreSQL tax review/identity guards.

The administrator creates synthetic identities only. All attack statements run
through the runtime login and accounting_app; no failure is accepted merely
because an arbitrary database error happened. Service races use independent
connections and events, never sleeps or production resources.
"""

from contextlib import contextmanager
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.accounting.database import Database, PostgresConnection
from app.accounting.models import BookCreate
from app.accounting.service import AccountingService, Actor
from app.tax.workpaper import TaxWorkpaperService
from scripts.pg_suite_target import load_suite_target, verify_suite_target

ADMIN_URL = os.getenv("ACCOUNTING_TEST_POSTGRES_URL")
APP_URL = os.getenv("ACCOUNTING_TEST_DATABASE_URL")
# Imported by the service-race module. Missing required-runner configuration
# must reach the fixture and fail, not disappear behind a module skip marker.
pytestmark = []


@pytest.fixture(scope="module")
def pg():
    import psycopg
    from scripts.migrate_accounting import migrate

    target = load_suite_target()
    if target is None:
        pytest.skip("N02 requires an explicit isolated PostgreSQL runner")
    target = verify_suite_target(target)
    assert (ADMIN_URL, APP_URL) == (target["admin"], target["app"]), (
        "N02 target changed after test collection"
    )
    migrate(target["admin"])
    database = Database(target["app"])
    yield database, psycopg
    database.close()


@pytest.fixture
def case(pg):
    db, psycopg = pg
    suffix = uuid4().hex[:12]
    firm = "n02-firm-" + suffix
    actors = {
        name: Actor(user_id=f"n02-{name}-{suffix}", firm_id=firm)
        for name in ("maker", "reviewer", "editor", "viewer")
    }
    with psycopg.connect(ADMIN_URL) as conn:
        for name, actor in actors.items():
            conn.execute(
                "INSERT INTO accounting.users(id,name,email) VALUES(%s,%s,%s)",
                (actor.user_id, name, f"{name}-{suffix}@example.test"),
            )
        conn.execute(
            "INSERT INTO accounting.firms(id,name,created_by) VALUES(%s,%s,%s)",
            (firm, "虚构N02事务所", actors["maker"].user_id),
        )
        for name, role in {
            "maker": "owner",
            "reviewer": "reviewer",
            "editor": "operator",
            "viewer": "operator",
        }.items():
            conn.execute(
                "INSERT INTO accounting.memberships(firm_id,user_id,role) VALUES(%s,%s,%s)",
                (firm, actors[name].user_id, role),
            )
    service = AccountingService(db)
    book = service.create_book(actors["maker"], BookCreate(name="虚构N02甲", period="2026-10"))
    other = service.create_book(actors["maker"], BookCreate(name="虚构N02乙", period="2026-10"))
    tax = TaxWorkpaperService(db)
    payload = {
        "effective_from": "2026-01",
        "taxpayer_type": "small_scale",
        "vat_rate_bp": 300,
        "surtax_rate_bp": 1200,
        "filing_cycle": "monthly",
        "reason": "虚构测试税率",
    }
    profile = tax.set_profile(actors["maker"], book["id"], payload)
    other_profile = tax.set_profile(actors["maker"], other["id"], payload)
    with psycopg.connect(ADMIN_URL) as conn:
        for name, role in (("reviewer", "reviewer"), ("editor", "editor"), ("viewer", "viewer")):
            conn.execute(
                "INSERT INTO accounting.book_access(book_id,firm_id,user_id,role) VALUES(%s,%s,%s,%s)",
                (book["id"], firm, actors[name].user_id, role),
            )
    result = SimpleNamespace(
        db=db,
        psycopg=psycopg,
        tax=tax,
        book=book,
        other=other,
        profile=profile,
        other_profile=other_profile,
        **actors,
    )
    # This fixture is an unreviewed native draft, not a claim of source verification.
    result.paper_id = "n02-paper-" + suffix
    _insert_paper(result, result.maker, id=result.paper_id)
    return result


@contextmanager
def native(case, actor):
    # Install the authenticated identity only. Deliberately skip Database's
    # Python write_lock and AccessControl so native SQL must hit DB backstops.
    from psycopg.rows import dict_row

    with case.psycopg.connect(APP_URL, autocommit=True, row_factory=dict_row) as raw:
        conn = PostgresConnection(raw)
        conn.begin(write=True, user_id=actor.user_id, firm_id=actor.firm_id)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _insert_paper(case, actor, **changes):
    values = {
        "id": "n02-copy-" + uuid4().hex,
        "firm_id": case.maker.firm_id,
        "company_id": case.book["company_id"],
        "book_id": case.book["id"],
        "period": "2026-10",
        "version": 1,
        "status": "draft",
        "profile_id": case.profile["id"],
        "output_net_cents": 100000,
        "output_tax_cents": 3000,
        "input_net_cents": 0,
        "input_tax_cents": 0,
        "tax_cents": 3000,
        "surtax_cents": 360,
        "credit_carry_cents": 0,
        "ledger_cents": 0,
        "variance_cents": 3000,
        "variance_reason": "",
        "lines_json": "[]",
        "source_digest": "0" * 64,
        "created_by": actor.user_id,
        "created_at": "2026-10-28T00:00:00+00:00",
    }
    with case.db.transaction(actor=case.maker) as conn:
        binding = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (case.book["id"],)
        ).fetchone()
    values["source_snapshot_json"] = json.dumps(
        {
            "schema": "n02-tax-v2",
            "period": "2026-10",
            "scope": {
                "firm_id": case.maker.firm_id,
                "company_id": case.book["company_id"],
                "book_id": case.book["id"],
                "period": "2026-10",
                "ledger_id": binding["ledger_id"],
            },
            "profile": {"id": case.profile["id"]},
            "vat_accounts": [],
            "invoices": [],
            "ledger": {"vouchers": [], "lines": []},
            "totals": [100000, 3000, 0, 0, 3000, 360, 0, 0],
        }
    )
    values.update(changes)
    with native(case, actor) as conn:
        columns = ",".join(values)
        marks = ",".join("?" for _ in values)
        conn.execute(
            f"INSERT INTO tax_workpapers({columns}) VALUES({marks})", tuple(values.values())
        )


def _review(case, actor, **changes):
    values = {
        "status": "reviewed",
        "reviewed_by": actor.user_id,
        "reviewed_at": "2026-10-28T01:00:00+00:00",
        "review_reason": "核对测试原件",
        "variance_reason": "虚构测试差异",
    }
    values.update(changes)
    with native(case, actor) as conn:
        return conn.execute(
            "UPDATE tax_workpapers SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
            (*values.values(), case.paper_id),
        ).rowcount


def _assert_draft(case):
    with case.db.transaction(actor=case.maker) as conn:
        row = conn.execute(
            "SELECT status,reviewed_by FROM tax_workpapers WHERE id=?", (case.paper_id,)
        ).fetchone()
    assert tuple(row.values()) == ("draft", None)


def test_editor_native_review_is_rejected(case):
    # UPDATE RLS hides the row from a non-reviewer; it must change zero rows.
    assert _review(case, case.editor) == 0
    _assert_draft(case)


def test_editor_native_profile_creation_is_rejected(case):
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with native(case, case.editor) as conn:
            conn.execute(
                "INSERT INTO tax_profiles(id,firm_id,company_id,effective_from,taxpayer_type,vat_rate_bp,"
                "surtax_rate_bp,filing_cycle,reason,created_by,created_at) VALUES(?,?,?,'2026-11','small_scale',300,0,'monthly','虚构修改税率',?,'now')",
                (uuid4().hex, case.maker.firm_id, case.book["company_id"], case.editor.user_id),
            )


@pytest.mark.parametrize("field", ["company_id", "profile_id", "period"])
def test_native_insert_scope_and_period_are_bound(case, field):
    changed = {
        "company_id": case.other["company_id"],
        "profile_id": case.other_profile["id"],
        "period": "2026-11",
    }[field]
    with pytest.raises(case.psycopg.errors.CheckViolation, match="scope|period|profile"):
        _insert_paper(case, case.maker, version=2, **{field: changed})


def test_postgres_refuses_a_forged_reviewed_row(case):
    with pytest.raises(case.psycopg.errors.CheckViolation, match="unreviewed draft"):
        _insert_paper(
            case,
            case.maker,
            version=2,
            status="reviewed",
            reviewed_by=case.reviewer.user_id,
            reviewed_at="now",
            review_reason="伪造复核意见",
        )


def test_postgres_refuses_identity_rewrite(case):
    with pytest.raises(case.psycopg.errors.CheckViolation, match="identity is immutable"):
        _review(case, case.reviewer, company_id=case.other["company_id"])
    _assert_draft(case)


def test_postgres_refuses_delete(case):
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with native(case, case.maker) as conn:
            conn.execute("DELETE FROM tax_workpapers WHERE id=?", (case.paper_id,))


def test_native_reviewer_cannot_impersonate_another_reviewer(case):
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        _review(case, case.reviewer, reviewed_by=case.maker.user_id)
    _assert_draft(case)


def test_native_draft_cannot_impersonate_maker(case):
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        _insert_paper(case, case.editor, version=2, created_by=case.maker.user_id)


def test_native_maker_cannot_self_review(case):
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege, match="own"):
        _review(case, case.maker)
    _assert_draft(case)


def test_native_authorized_independent_review_succeeds_once(case):
    assert _review(case, case.reviewer) == 1
    with pytest.raises(case.psycopg.errors.CheckViolation, match="Only a draft"):
        _review(case, case.reviewer)


def test_viewer_cannot_review_or_create_draft(case):
    assert _review(case, case.viewer) == 0
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        _insert_paper(case, case.viewer, version=2)


def test_native_profile_is_append_only(case):
    with pytest.raises(case.psycopg.errors.InsufficientPrivilege):
        with native(case, case.maker) as conn:
            conn.execute(
                "UPDATE tax_profiles SET vat_rate_bp=600 WHERE id=?", (case.profile["id"],)
            )


def test_source_snapshot_cannot_be_rewritten(case):
    with pytest.raises(case.psycopg.errors.CheckViolation, match="source snapshot"):
        _review(case, case.reviewer, source_snapshot_json='{"forged":true}')


@pytest.mark.parametrize("snapshot", ["", "not-json", "{}", '{"schema":"n02-tax-v1"}'])
def test_native_review_rejects_missing_or_unsupported_snapshot(case, snapshot):
    paper = "n02-legacy-" + uuid4().hex
    with pytest.raises(case.psycopg.errors.CheckViolation, match="source snapshot"):
        _insert_paper(case, case.maker, id=paper, version=2, source_snapshot_json=snapshot)
        case.paper_id = paper
        _review(case, case.reviewer)
    _assert_draft(case)


@pytest.mark.parametrize(
    "field", ["firm_id", "company_id", "book_id", "period", "ledger_id", "profile_id"]
)
def test_native_review_rejects_snapshot_scope_spoof(case, field):
    with case.db.transaction(actor=case.maker) as conn:
        source = json.loads(
            conn.execute(
                "SELECT source_snapshot_json FROM tax_workpapers WHERE id=?", (case.paper_id,)
            ).fetchone()["source_snapshot_json"]
        )
    if field == "profile_id":
        source["profile"]["id"] = case.other_profile["id"]
    else:
        source["scope"][field] = "wrong-scope"
    paper = "n02-spoof-" + uuid4().hex
    _insert_paper(case, case.maker, id=paper, version=2, source_snapshot_json=json.dumps(source))
    case.paper_id = paper
    with pytest.raises(case.psycopg.errors.CheckViolation, match="source snapshot"):
        _review(case, case.reviewer)
    _assert_draft(case)


@pytest.mark.parametrize(
    "changes",
    [
        {"tax_cents": 3001},
        {"lines_json": '[{"forged":true}]'},
        {"variance_cents": 2999},
    ],
)
def test_native_insert_cannot_substitute_calculated_fields(case, changes):
    with pytest.raises(case.psycopg.errors.CheckViolation, match="snapshot|variance"):
        _insert_paper(case, case.maker, version=2, **changes)


@pytest.mark.parametrize("token", [True, 1.5, "100000", 100000.0])
def test_native_snapshot_totals_are_strict_integers(case, token):
    with case.db.transaction(actor=case.maker) as conn:
        source = json.loads(
            conn.execute(
                "SELECT source_snapshot_json FROM tax_workpapers WHERE id=?", (case.paper_id,)
            ).fetchone()["source_snapshot_json"]
        )
    source["totals"][0] = token
    with pytest.raises(case.psycopg.errors.CheckViolation, match="snapshot"):
        _insert_paper(case, case.maker, version=2, source_snapshot_json=json.dumps(source))
