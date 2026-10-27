"""Historical migration debt must match an exact, hashed signature.

The checker runs on every push. Migrations that were already committed keep
their bytes, so their non-idempotent statements are registered instead of
rewritten. Registration is exact: a changed file, a new violation, a missing
violation, a duplicate entry or an entry for a clean file all fail.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import check_migration_idempotency as checker

FUNCTION = "CREATE FUNCTION accounting.f() RETURNS INT AS $$ SELECT 1 $$;"
TRIGGER = (
    "CREATE TRIGGER t BEFORE UPDATE ON accounting.x FOR EACH ROW EXECUTE FUNCTION accounting.f();"
)
DROP_T = "DROP TRIGGER IF EXISTS t ON accounting.x;"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _layout(tmp_path: Path, files: dict[str, str], entries: list[dict]) -> dict:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for name, sql in files.items():
        # Bytes, not text: Windows would otherwise translate newlines and the
        # hash of the registered entry would no longer match the file.
        (migrations / name).write_bytes(sql.encode("utf-8"))
    ledger = tmp_path / "legacy.json"
    ledger.write_text(json.dumps({"version": 1, "entries": entries}), encoding="utf-8")
    return {
        "migrations": migrations,
        "ledger": ledger,
    }


def _entry(name: str, sql: str, violations: list[dict], reason: str = "历史已共享迁移") -> dict:
    return {
        "file": name,
        "sha256": _sha(sql),
        "reason": reason,
        "violations": violations,
    }


def _check(tmp_path: Path, files: dict[str, str], entries: list[dict]) -> list[str]:
    layout = _layout(tmp_path, files, entries)
    return checker.check(migrations=layout["migrations"], ledger_path=layout["ledger"], baseline=0)


def test_registered_exact_debt_passes(tmp_path: Path) -> None:
    entries = [
        _entry(
            "136_x.sql",
            FUNCTION,
            [{"rule": "function_not_create_or_replace", "object": "accounting.f"}],
        )
    ]
    assert _check(tmp_path, {"136_x.sql": FUNCTION}, entries) == []


def test_changed_registered_file_fails(tmp_path: Path) -> None:
    entries = [
        _entry(
            "136_x.sql",
            FUNCTION,
            [{"rule": "function_not_create_or_replace", "object": "accounting.f"}],
        )
    ]
    changed = FUNCTION + "\nSELECT 1;"
    problems = _check(tmp_path, {"136_x.sql": changed}, entries)
    assert any("136_x.sql" in item and "changed" in item for item in problems), problems


def test_new_migration_with_violation_fails(tmp_path: Path) -> None:
    problems = _check(tmp_path, {"137_y.sql": TRIGGER}, [])
    assert any("137_y.sql" in item and "unregistered" in item for item in problems), problems


def test_registered_violation_missing_fails(tmp_path: Path) -> None:
    entries = [
        _entry(
            "136_x.sql",
            FUNCTION,
            [
                {"rule": "function_not_create_or_replace", "object": "accounting.f"},
                {"rule": "trigger_without_drop_if_exists", "object": "t on accounting.x"},
            ],
        )
    ]
    problems = _check(tmp_path, {"136_x.sql": FUNCTION}, entries)
    assert any("stale" in item for item in problems), problems


def test_extra_violation_in_registered_file_fails(tmp_path: Path) -> None:
    sql = FUNCTION + "\n" + TRIGGER
    entries = [
        _entry(
            "136_x.sql", sql, [{"rule": "function_not_create_or_replace", "object": "accounting.f"}]
        )
    ]
    problems = _check(tmp_path, {"136_x.sql": sql}, entries)
    assert any("unregistered" in item for item in problems), problems


def test_duplicate_ledger_entry_fails(tmp_path: Path) -> None:
    violation = [{"rule": "function_not_create_or_replace", "object": "accounting.f"}]
    entries = [_entry("136_x.sql", FUNCTION, violation), _entry("136_x.sql", FUNCTION, violation)]
    problems = _check(tmp_path, {"136_x.sql": FUNCTION}, entries)
    assert any("duplicate" in item for item in problems), problems


def test_entry_for_clean_file_fails(tmp_path: Path) -> None:
    clean = "CREATE OR REPLACE FUNCTION accounting.f() RETURNS INT AS $$ SELECT 1 $$;"
    entries = [
        _entry(
            "136_x.sql",
            clean,
            [{"rule": "function_not_create_or_replace", "object": "accounting.f"}],
        )
    ]
    problems = _check(tmp_path, {"136_x.sql": clean}, entries)
    assert any("stale" in item for item in problems), problems


def test_trigger_drop_must_match_name_and_table(tmp_path: Path) -> None:
    sql = "DROP TRIGGER IF EXISTS other ON accounting.x;" + TRIGGER
    problems = _check(tmp_path, {"136_x.sql": sql}, [])
    assert any("trigger_without_drop_if_exists" in item for item in problems), problems


def test_two_creates_need_two_drops(tmp_path: Path) -> None:
    second = TRIGGER.replace("accounting.x", "accounting.y")
    sql = DROP_T + TRIGGER + second
    problems = _check(tmp_path, {"136_x.sql": sql}, [])
    assert any("accounting.y" in item for item in problems), problems


def test_ledger_unknown_rule_fails(tmp_path: Path) -> None:
    entries = [_entry("136_x.sql", FUNCTION, [{"rule": "made_up_rule", "object": "accounting.f"}])]
    problems = _check(tmp_path, {"136_x.sql": FUNCTION}, entries)
    assert any("invalid" in item for item in problems), problems


def test_after_may_not_raise_baseline(capsys: pytest.CaptureFixture[str]) -> None:
    code = checker.main(["--after", str(checker.BASELINE_VERSION + 1)])
    assert code == 2
    assert "扫描起点" in capsys.readouterr().err


def _layout_v2(
    tmp_path: Path, files: dict[str, str], entries: list[dict], approved: list[dict]
) -> dict:
    layout = _layout(tmp_path, files, entries)
    layout["ledger"].write_text(
        json.dumps({"version": 2, "approved": approved, "entries": entries}), encoding="utf-8"
    )
    return layout


def test_new_migration_cannot_buy_exemption_by_writing_its_own_entry(tmp_path: Path) -> None:
    """台账只覆盖获批的历史文件；新增迁移即使「哈希完全匹配」也必须被拒绝。"""

    entry = _entry(
        "137_y.sql",
        TRIGGER,
        [{"rule": "trigger_without_drop_if_exists", "object": "t on accounting.x"}],
    )
    approved = [{"file": "136_x.sql", "sha256": _sha(FUNCTION), "basis": "Q01 验收记录 §3"}]
    layout = _layout_v2(tmp_path, {"136_x.sql": FUNCTION, "137_y.sql": TRIGGER}, [entry], approved)
    problems = checker.check(
        migrations=layout["migrations"], ledger_path=layout["ledger"], baseline=0
    )
    assert any("137_y.sql" in item and "approved historical set" in item for item in problems), (
        problems
    )


def test_approved_digest_must_match_the_entry(tmp_path: Path) -> None:
    entry = _entry(
        "136_x.sql",
        FUNCTION,
        [{"rule": "function_not_create_or_replace", "object": "accounting.f"}],
    )
    approved = [{"file": "136_x.sql", "sha256": "0" * 64, "basis": "Q01 验收记录 §3"}]
    layout = _layout_v2(tmp_path, {"136_x.sql": FUNCTION}, [entry], approved)
    problems = checker.check(
        migrations=layout["migrations"], ledger_path=layout["ledger"], baseline=0
    )
    assert any("approved digest does not match" in item for item in problems), problems


def test_approved_file_without_ledger_entry_fails(tmp_path: Path) -> None:
    clean = "CREATE OR REPLACE FUNCTION accounting.f() RETURNS INT AS $$ SELECT 1 $$;"
    approved = [{"file": "136_x.sql", "sha256": _sha(clean), "basis": "Q01 验收记录 §3"}]
    layout = _layout_v2(tmp_path, {"136_x.sql": clean}, [], approved)
    problems = checker.check(
        migrations=layout["migrations"], ledger_path=layout["ledger"], baseline=0
    )
    assert any("no matching ledger entry" in item for item in problems), problems


def test_v1_ledger_cannot_be_verified_when_approvals_required(tmp_path: Path) -> None:
    entries = [
        _entry(
            "136_x.sql",
            FUNCTION,
            [{"rule": "function_not_create_or_replace", "object": "accounting.f"}],
        )
    ]
    layout = _layout(tmp_path, {"136_x.sql": FUNCTION}, entries)
    problems = checker.check(
        migrations=layout["migrations"],
        ledger_path=layout["ledger"],
        baseline=0,
        require_approvals=True,
    )
    assert any("version 2" in item for item in problems), problems
