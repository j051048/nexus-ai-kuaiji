"""Scanner contracts: cross-platform digests and real SQL structure.

The checker must reach the same verdict on a Windows checkout (CRLF) and a
Linux checkout (LF) of the same migration, and it must not be fooled by
comments, string literals, quoted identifiers or constraint triggers. Each
test states the defect it reproduces.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import check_migration_idempotency as checker

FUNCTION = "CREATE FUNCTION accounting.f() RETURNS INT AS $$ SELECT 1 $$;"
TRIGGER = (
    "CREATE TRIGGER t BEFORE UPDATE ON accounting.x FOR EACH ROW EXECUTE FUNCTION accounting.f();"
)


def _normalized_sha(sql: str) -> str:
    return hashlib.sha256(sql.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def _layout(tmp_path: Path, files: dict[str, bytes | str], entries: list[dict]) -> dict:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name, sql in files.items():
        payload = sql.encode("utf-8") if isinstance(sql, str) else sql
        (migrations / name).write_bytes(payload)
    ledger = tmp_path / "legacy.json"
    ledger.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    return {"migrations": migrations, "ledger": ledger}


def _entry(name: str, sql: str, violations: list[dict], reason: str = "历史已共享迁移") -> dict:
    return {
        "file": name,
        "sha256": _normalized_sha(sql),
        "reason": reason,
        "violations": violations,
    }


def _check(tmp_path: Path, files: dict[str, bytes | str], entries: list[dict]) -> list[str]:
    layout = _layout(tmp_path, files, entries)
    return checker.check(migrations=layout["migrations"], ledger_path=layout["ledger"], baseline=0)


def _rules(sql: str) -> list[tuple[str, str]]:
    return [(item.rule, item.object) for item in checker.scan_sql("136_x.sql", sql)]


def test_crlf_and_lf_checkouts_agree(tmp_path: Path) -> None:
    """A Windows CRLF checkout must not look tampered against an LF digest."""
    crlf = FUNCTION.replace("\n", "\r\n") + "\r\n" + TRIGGER.replace("\n", "\r\n")
    entries = [
        _entry(
            "136_x.sql",
            FUNCTION + "\n" + TRIGGER,
            [
                {"rule": "function_not_create_or_replace", "object": "accounting.f"},
                {"rule": "trigger_without_drop_if_exists", "object": "t on accounting.x"},
            ],
        )
    ]
    problems = _check(tmp_path, {"136_x.sql": crlf.encode("utf-8")}, entries)
    assert problems == [], problems


def test_real_content_change_still_fails(tmp_path: Path) -> None:
    entries = [
        _entry(
            "136_x.sql",
            FUNCTION,
            [{"rule": "function_not_create_or_replace", "object": "accounting.f"}],
        )
    ]
    changed = FUNCTION.replace("SELECT 1", "SELECT 2")
    problems = _check(tmp_path, {"136_x.sql": changed}, entries)
    assert any("sha256 mismatch" in item for item in problems), problems


def test_quoted_identifiers_are_detected() -> None:
    sql = 'CREATE FUNCTION "accounting"."Order Sync"() RETURNS INT AS $$ SELECT 1 $$;'
    assert ("function_not_create_or_replace", "accounting.Order Sync") in _rules(sql)


def test_constraint_trigger_is_detected() -> None:
    sql = (
        "CREATE CONSTRAINT TRIGGER t AFTER INSERT ON accounting.x "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION accounting.f();"
    )
    assert ("trigger_without_drop_if_exists", "t on accounting.x") in _rules(sql)


def test_drop_inside_comment_does_not_count() -> None:
    sql = "-- DROP TRIGGER IF EXISTS t ON accounting.x;\n" + TRIGGER
    assert ("trigger_without_drop_if_exists", "t on accounting.x") in _rules(sql)


def test_create_inside_string_literal_is_not_counted() -> None:
    sql = "DO $$ BEGIN EXECUTE 'CREATE TRIGGER t BEFORE UPDATE ON accounting.x FOR EACH ROW EXECUTE FUNCTION accounting.f()'; END $$;"
    assert _rules(sql) == []


def test_drop_on_another_table_does_not_count() -> None:
    sql = "DROP TRIGGER IF EXISTS t ON accounting.y;\n" + TRIGGER
    assert ("trigger_without_drop_if_exists", "t on accounting.x") in _rules(sql)


def test_one_drop_covers_only_one_create() -> None:
    sql = "DROP TRIGGER IF EXISTS t ON accounting.x;\n" + TRIGGER + "\n" + TRIGGER
    rules = _rules(sql)
    assert rules == [("trigger_without_drop_if_exists", "t on accounting.x")], rules


def _layout_v2(
    tmp_path: Path,
    files: dict[str, str],
    entries: list[dict],
    approved: list[dict] | None,
) -> dict:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name, sql in files.items():
        (migrations / name).write_bytes(sql.encode("utf-8"))
    ledger = tmp_path / "legacy.json"
    document: dict = {"version": 2 if approved is not None else 1, "entries": entries}
    if approved is not None:
        document["approved"] = approved
    ledger.write_text(json.dumps(document), encoding="utf-8")
    return {"migrations": migrations, "ledger": ledger}


def test_new_violating_migration_cannot_launder_a_ledger_entry(tmp_path: Path) -> None:
    """A ledger entry for a brand-new migration must not create an exemption.

    The defect this reproduces: the ledger was the only source of truth, so
    writing a matching entry for a new violation made the gate pass. Now the
    approved set decides which files may carry debt at all.
    """
    sql = FUNCTION
    entry = _entry(
        "170_new.sql",
        sql,
        [{"rule": "function_not_create_or_replace", "object": "accounting.f"}],
    )
    layout = _layout_v2(tmp_path, {"170_new.sql": sql}, [entry], approved=[])
    problems = checker.check(
        migrations=layout["migrations"],
        ledger_path=layout["ledger"],
        baseline=0,
        require_approvals=True,
    )
    assert any("not in the approved historical set" in item for item in problems), problems


def test_approved_digest_must_match_the_pinned_bytes(tmp_path: Path) -> None:
    sql = FUNCTION
    entry = _entry(
        "170_new.sql",
        sql,
        [{"rule": "function_not_create_or_replace", "object": "accounting.f"}],
    )
    approved = [
        {"file": "170_new.sql", "sha256": "0" * 64, "basis": "已批准", "approved_on": "2026-10-25"}
    ]
    layout = _layout_v2(tmp_path, {"170_new.sql": sql}, [entry], approved=approved)
    problems = checker.check(
        migrations=layout["migrations"],
        ledger_path=layout["ledger"],
        baseline=0,
        require_approvals=True,
    )
    assert any("approved digest does not match" in item for item in problems), problems


def test_v1_ledger_cannot_satisfy_approval_requirement(tmp_path: Path) -> None:
    sql = FUNCTION
    entry = _entry(
        "170_new.sql",
        sql,
        [{"rule": "function_not_create_or_replace", "object": "accounting.f"}],
    )
    layout = _layout_v2(tmp_path, {"170_new.sql": sql}, [entry], approved=None)
    problems = checker.check(
        migrations=layout["migrations"],
        ledger_path=layout["ledger"],
        baseline=0,
        require_approvals=True,
    )
    assert any("approved historical files" in item for item in problems), problems


def test_dynamic_ddl_is_reported_for_manual_review(tmp_path: Path) -> None:
    """Unsupported constructs must surface as 需专项检查, never as a pass."""
    sql = "DO $$ BEGIN EXECUTE format('CREATE TABLE %I (id INT)', 'accounting.x'); END $$;\n"
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "170_new.sql").write_bytes(sql.encode("utf-8"))
    items = checker.review_findings(migrations)
    kinds = {(item.file, item.kind) for item in items}
    assert ("170_new.sql", "dynamic_ddl") in kinds, items
    assert ("170_new.sql", "do_block") in kinds, items
    for item in items:
        assert "需专项检查" in item.render()
        assert "PostgreSQL" in item.render()


def test_plain_migration_has_no_review_items(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "170_plain.sql").write_bytes(b"ALTER TABLE accounting.x ADD COLUMN y INT;\n")
    assert checker.review_findings(migrations) == []
