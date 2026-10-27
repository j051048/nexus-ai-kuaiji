"""Lint PostgreSQL migrations for unsafe, non-idempotent object changes.

New migrations must be replayable: functions are created with ``OR REPLACE``
and every trigger is dropped before it is created. Migrations that were already
committed keep their exact bytes, so their debt is registered in
``scripts/migration_idempotency_legacy.json`` together with the file hash and
the exact violation signature. Any drift fails: a changed file, a new
violation, a missing violation, a duplicate entry or an entry for a clean file.

Registration is two-step on purpose. ``approved`` lists the historical files
this project has explicitly accepted as immutable debt; ``entries`` records what
each of them currently violates. A ledger entry whose file is not in
``approved`` is rejected, so adding a violation to a *new* migration cannot be
laundered by writing a matching ledger entry.

This is a static text check. It proves nothing about statements it cannot see
(dynamic DDL built with ``EXECUTE``/``format()``, or constructs outside the
supported grammar); those are reported as ``需专项检查`` lines and still need a
real PostgreSQL replay. Passing this script means "no known static pattern is
missing"; it does not mean "the migration is idempotent".
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "nexus_backend" / "migrations" / "postgres"
LEDGER = ROOT / "scripts" / "migration_idempotency_legacy.json"
BASELINE_VERSION = 135
# Historical debt is a closed set, not a self-service list. The ledger may not
# grow: a new migration at or after ``HISTORY_MAX_VERSION`` can never be
# approved, and the registered digests pin exactly what "historical" means.
HISTORY_MAX_VERSION = 161
APPROVED_HISTORICAL = {
    "153_c01_auxiliary_entity_contract.sql": "b6a82ff5c2f17683027d2c8b965e4c43d980b4c1d0f6cc7220643190284eae71",
    "154_bank_coverage_evidence.sql": "046114cdddfd901a59ede5ac59ac5d3b70ffdd052f5bbed76dfffe14a6ecd140",
    "155_monthly_delivery_packages.sql": "8d6fd06abe0453c0a2e891da4b68dba99eaa86a6a931c179dd4a7d4d45da1d52",
    "157_c01_exact_reconciliation.sql": "ee2ec8c85c355936a05b2b52e8bf6ea6c94982912db182e7bfd15644cb6518b4",
    "158_c01_period_bridge.sql": "2e58ff7b8910b7713077ae3368a708306b6ad2decee9ba1b9853fb4a757e0d43",
    "159_recovery_release_audit.sql": "e3d47e2cb15e7eb6dacb268da9a177e6065105e36b4a096d60f0f5c48097508e",
    "160_c01_evidence_reference_freeze.sql": "d830fa656e9923d982b9a738795868f5b5dbad1f427905ca771efaeeebad54d0",
}
APPROVED_DIGEST = "557bf0e0ba7c68a31e3f35fc082c1daac9531579fb7b3fc7f594f2274ef7d7c9"

RULE_FUNCTION = "function_not_create_or_replace"
RULE_FUNCTIONDEF = "functiondef_rewrite"
RULE_TRIGGER = "trigger_without_drop_if_exists"
RULES = frozenset({RULE_FUNCTION, RULE_FUNCTIONDEF, RULE_TRIGGER})

# Constructs the scanner deliberately does not evaluate. Seeing them is not a
# failure by itself (a ``DO`` block may be harmless), but it must never be
# counted as "verified idempotent" either.
REVIEW_EXECUTE = re.compile(r"\bEXECUTE\s+", re.IGNORECASE)
REVIEW_DO = re.compile(r"\bDO\s+\$", re.IGNORECASE)
REVIEW_FORMAT = re.compile(r"\bformat\s*\(", re.IGNORECASE)

IDENT = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
QUALIFIED = rf"{IDENT}(?:\s*\.\s*{IDENT})*"

FUNCTION = re.compile(rf"\bCREATE\s+(OR\s+REPLACE\s+)?FUNCTION\s+({QUALIFIED})", re.IGNORECASE)
TRIGGER = re.compile(
    rf"\b(CREATE|DROP)\s+(CONSTRAINT\s+)?TRIGGER\s+(IF\s+EXISTS\s+)?({IDENT})",
    re.IGNORECASE,
)
ON_TABLE = re.compile(rf"\bON\s+({QUALIFIED})", re.IGNORECASE)


def _unquote(name: str) -> str:
    parts = []
    for part in re.split(r"\s*\.\s*", name.strip()):
        if part.startswith('"') and part.endswith('"') and len(part) >= 2:
            parts.append(part[1:-1].replace('""', '"'))
        else:
            parts.append(part)
    return ".".join(parts)


def _mask(sql: str) -> str:
    """Blank out comments and literals so quoted text never looks like DDL."""
    out = list(sql)
    index, length = 0, len(sql)
    while index < length:
        char = sql[index]
        if sql.startswith("--", index):
            end = sql.find("\n", index)
            end = length if end < 0 else end
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            end = length if end < 0 else end + 2
        elif char == "'":
            end = index + 1
            while end < length:
                if sql[end] == "'":
                    if end + 1 < length and sql[end + 1] == "'":
                        end += 2
                        continue
                    end += 1
                    break
                end += 1
        elif char == "$" and (dollar := re.match(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$", sql[index:])):
            tag = dollar.group(0)
            closing = sql.find(tag, index + len(tag))
            end = length if closing < 0 else closing + len(tag)
        else:
            index += 1
            continue
        for position in range(index, min(end, length)):
            out[position] = " "
        index = end
    return "".join(out)


def _statement_end(masked: str, start: int) -> int:
    end = masked.find(";", start)
    return len(masked) if end < 0 else end


def _trigger_table(masked: str, start: int) -> str:
    statement = masked[start : _statement_end(masked, start)]
    match = ON_TABLE.search(statement)
    return _unquote(match.group(1)) if match else ""


def _mask_comments(sql: str) -> str:
    """Blank comments only, keeping keywords and dollar-quoted bodies visible."""
    out = list(sql)
    index, length = 0, len(sql)
    while index < length:
        if sql.startswith("--", index):
            end = sql.find("\n", index)
            end = length if end < 0 else end
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            end = length if end < 0 else end + 2
        else:
            index += 1
            continue
        for position in range(index, min(end, length)):
            out[position] = " "
        index = end
    return "".join(out)


@dataclass(frozen=True, order=True)
class ReviewItem:
    file: str
    kind: str
    detail: str

    def render(self) -> str:
        return f"{self.file}: 需专项检查: {self.kind}（{self.detail}），静态扫描无法判定，需真实 PostgreSQL 重放"


def review_findings(directory: Path | None = None) -> list[ReviewItem]:
    """List constructs the scanner cannot judge; never treat them as verified."""
    root = directory or MIGRATIONS
    found: list[ReviewItem] = []
    for path in sorted(root.glob("*.sql")):
        try:
            version = int(path.name.split("_", 1)[0])
        except ValueError:
            continue
        if version <= BASELINE_VERSION:
            continue
        text = _mask_comments(read_sql(path))
        if REVIEW_EXECUTE.search(text):
            found.append(ReviewItem(path.name, "dynamic_ddl", "EXECUTE"))
        if REVIEW_DO.search(text):
            found.append(ReviewItem(path.name, "do_block", "DO $$"))
        if REVIEW_FORMAT.search(text):
            found.append(ReviewItem(path.name, "dynamic_ddl", "format()"))
    return found


def scan_sql(name: str, sql: str) -> list[Violation]:
    """Return every non-idempotent statement in one migration, in file order."""
    masked = _mask(sql)
    found: list[Violation] = []
    for match in FUNCTION.finditer(masked):
        if not match.group(1):
            found.append(Violation(name, RULE_FUNCTION, _unquote(match.group(2))))
    upper = masked.upper()
    if "PG_GET_FUNCTIONDEF" in upper and re.search(r"\bREPLACE\s*\(", upper):
        found.append(Violation(name, RULE_FUNCTIONDEF, "pg_get_functiondef"))
    pending: dict[tuple[str, str], int] = {}
    for match in TRIGGER.finditer(masked):
        action = match.group(1).upper()
        if_exists = match.group(3)
        trigger = _unquote(match.group(4))
        table = _trigger_table(masked, match.end()).lower()
        key = (trigger.lower(), table)
        if action == "DROP":
            # A bare DROP TRIGGER is not replayable when the trigger is absent.
            if if_exists:
                pending[key] = pending.get(key, 0) + 1
            continue
        if pending.get(key, 0) > 0:
            pending[key] -= 1
            continue
        found.append(Violation(name, RULE_TRIGGER, f"{trigger} on {table or '?'}"))
    return found


@dataclass(frozen=True, order=True)
class Violation:
    file: str
    rule: str
    object: str

    def render(self) -> str:
        return f"{self.file}: {self.rule}: {self.object}"

    def signature(self) -> dict[str, str]:
        return {"rule": self.rule, "object": self.object}


def violations(name: str, sql: str) -> list[str]:
    """Legacy helper retained for existing callers: human-readable violations."""
    return [item.render() for item in scan_sql(name, sql)]


def read_sql(path: Path) -> str:
    """Decode a migration with the same newline policy as the runtime digest."""
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def _file_sha256(path: Path) -> str:
    # Hash the decoded text, not the raw bytes: a CRLF checkout and an LF
    # checkout of the same migration describe the same database (see
    # accounting.database.migration_digest, which normalises CRLF too).
    return hashlib.sha256(read_sql(path).encode("utf-8")).hexdigest()


def _ledger_document(ledger_path: Path) -> tuple[list[dict], dict[str, str] | None, list[str]]:
    """Return (entries, approved, problems); approved is None for a v1 ledger."""
    if not ledger_path.exists():
        return [], None, [f"{ledger_path.name}: invalid ledger: missing file"]
    try:
        document = json.loads(ledger_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [], None, [f"{ledger_path.name}: invalid ledger: {error}"]
    if not isinstance(document, dict):
        return [], None, [f"{ledger_path.name}: invalid ledger: must be an object"]
    version = document.get("version")
    if version not in (1, 2):
        return [], None, [f"{ledger_path.name}: invalid ledger: version must be 1 or 2"]
    entries = document.get("entries")
    if not isinstance(entries, list):
        return [], None, [f"{ledger_path.name}: invalid ledger: entries must be a list"]
    approved: dict[str, str] | None = None
    problems: list[str] = []
    if "approved" in document:
        items = document["approved"]
        if not isinstance(items, list):
            return [], None, [f"{ledger_path.name}: invalid ledger: approved must be a list"]
        approved = {}
        for item in items:
            if not isinstance(item, dict):
                problems.append("invalid approved entry: not an object")
                continue
            file = item.get("file")
            sha = item.get("sha256")
            basis = item.get("basis")
            if not isinstance(file, str) or not file:
                problems.append("invalid approved entry: file")
                continue
            if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
                problems.append(f"{file}: invalid approved entry: sha256")
                continue
            if not isinstance(basis, str) or not basis.strip():
                problems.append(f"{file}: invalid approved entry: basis")
                continue
            if file in approved:
                problems.append(f"{file}: duplicate approved entry")
                continue
            approved[file] = sha
    return entries, approved, problems


def _validate_entry(entry: object) -> list[str]:
    if not isinstance(entry, dict):
        return ["invalid ledger entry: not an object"]
    file = entry.get("file")
    sha = entry.get("sha256")
    reason = entry.get("reason")
    items = entry.get("violations")
    if not isinstance(file, str) or not file:
        return ["invalid ledger entry: file"]
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        return [f"{file}: invalid ledger entry: sha256"]
    if not isinstance(reason, str) or not reason.strip():
        return [f"{file}: invalid ledger entry: reason"]
    if not isinstance(items, list) or not items:
        return [f"{file}: invalid ledger entry: violations"]
    problems = []
    for item in items:
        if not isinstance(item, dict):
            problems.append(f"{file}: invalid ledger entry: violation")
        elif item.get("rule") not in RULES:
            problems.append(f"{file}: invalid ledger entry: rule {item.get('rule')!r}")
        elif not isinstance(item.get("object"), str) or not item.get("object"):
            problems.append(f"{file}: invalid ledger entry: object")
    return problems


def _approved_set_problems(approved: dict[str, str]) -> list[str]:
    """Verify the ledger's ``approved`` list against the shipped registry.

    Without this the ledger could launder a new migration by listing it in both
    ``approved`` and ``entries``: every other check is then satisfied by the
    file's own contents. The registry is code, so extending it requires a review
    of this file, not an edit of data next to the migrations.
    """

    problems: list[str] = []
    for name in sorted(set(approved) - set(APPROVED_HISTORICAL)):
        problems.append(
            f"{name}: not an approved historical file; the approved historical set "
            "cannot be extended from the ledger"
        )
    for name in sorted(set(APPROVED_HISTORICAL) - set(approved)):
        problems.append(f"{name}: approved historical file has no matching ledger entry")
    for name, digest in sorted(approved.items()):
        expected = APPROVED_HISTORICAL.get(name)
        if expected is not None and expected != digest:
            problems.append(
                f"{name}: approved digest differs from the registered historical digest"
            )
    lines = sorted(f"{name}:{digest}" for name, digest in approved.items())
    aggregate = hashlib.sha256("\n".join(lines).encode()).hexdigest()
    if aggregate != APPROVED_DIGEST:
        problems.append(
            f"approved historical set digest does not match the registered set ({aggregate})"
        )
    for name in sorted(approved):
        try:
            version = int(name.split("_", 1)[0])
        except ValueError:
            problems.append(f"{name}: approved entry has no migration version prefix")
            continue
        if version >= HISTORY_MAX_VERSION:
            problems.append(
                f"{name}: approved historical set must stay below {HISTORY_MAX_VERSION}"
            )
    return problems


def evaluate(
    actual: list[Violation],
    entries: list[dict],
    migrations: Path,
    approved: dict[str, str] | None = None,
) -> list[str]:
    problems: list[str] = []
    if approved is not None:
        problems.extend(_approved_set_problems(approved))
    by_file: dict[str, list[Violation]] = {}
    for item in actual:
        by_file.setdefault(item.file, []).append(item)
    registered: dict[str, dict] = {}
    for entry in entries:
        invalid = _validate_entry(entry)
        if invalid:
            problems.extend(invalid)
            continue
        name = entry["file"]
        if name in registered:
            problems.append(f"{name}: duplicate ledger entry")
            continue
        if not (migrations / name).exists():
            problems.append(f"{name}: invalid ledger entry: file does not exist")
            continue
        if approved is not None:
            if name not in approved:
                # A new migration cannot buy an exemption by writing its own
                # ledger entry: only explicitly approved historical files may
                # be registered.
                problems.append(f"{name}: ledger entry is not in the approved historical set")
                continue
            if approved[name] != entry["sha256"]:
                problems.append(f"{name}: approved digest does not match the ledger entry")
                continue
        registered[name] = entry
    if approved is not None:
        for name in sorted(set(approved) - set(registered)):
            problems.append(f"{name}: approved historical file has no matching ledger entry")
    for name, entry in registered.items():
        path = migrations / name
        if _file_sha256(path) != entry["sha256"]:
            problems.append(f"{name}: changed since registration (sha256 mismatch)")
            continue
        expected = {(item["rule"], item["object"]) for item in entry["violations"]}
        present = {(item.rule, item.object) for item in by_file.get(name, [])}
        for rule, key in sorted(present - expected):
            problems.append(f"{name}: unregistered violation {rule} {key}")
        for rule, key in sorted(expected - present):
            problems.append(f"{name}: stale registration {rule} {key} is no longer present")
    for name in sorted(by_file):
        if name not in registered:
            for item in by_file[name]:
                problems.append(f"{name}: unregistered violation {item.rule} {item.object}")
    return problems


def check(
    baseline: int = BASELINE_VERSION,
    *,
    migrations: Path | None = None,
    ledger_path: Path | None = None,
    require_approvals: bool = False,
) -> list[str]:
    directory = migrations or MIGRATIONS
    ledger = ledger_path or LEDGER
    actual: list[Violation] = []
    for path in sorted(directory.glob("*.sql")):
        try:
            version = int(path.name.split("_", 1)[0])
        except ValueError:
            continue
        if version <= baseline:
            continue
        actual.extend(scan_sql(path.name, read_sql(path)))
    entries, approved, problems = _ledger_document(ledger)
    if problems:
        return problems
    if require_approvals and approved is None:
        return [
            f"{ledger.name}: ledger must declare the approved historical files "
            "(version 2 with an 'approved' list); a v1 ledger cannot be verified"
        ]
    return evaluate(actual, entries, directory, approved)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--after", type=int, default=BASELINE_VERSION)
    parser.add_argument("--migrations", type=Path, default=MIGRATIONS)
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument(
        "--strict-review",
        action="store_true",
        help="fail when a migration contains constructs only a real PostgreSQL replay can judge",
    )
    args = parser.parse_args(argv)
    if args.after > BASELINE_VERSION:
        print(
            f"扫描起点不得高于 {BASELINE_VERSION}：提高起点会跳过历史迁移检查。",
            file=sys.stderr,
        )
        return 2
    problems = check(
        args.after,
        migrations=args.migrations,
        ledger_path=args.ledger,
        require_approvals=True,
    )
    for problem in problems:
        print(problem)
    review = review_findings(args.migrations)
    for item in review:
        print(item.render())
    if review:
        print(
            f"共 {len(review)} 处需专项检查：静态扫描不判定这些动态 DDL，"
            "必须在 PostgreSQL 16/17 空库重放后才能视为幂等。",
            file=sys.stderr,
        )
    if problems:
        return 1
    if args.strict_review and review:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
