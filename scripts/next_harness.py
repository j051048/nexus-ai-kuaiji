"""NEXT execution harness: candidate-bound runs, owned resources, crash recovery.

Every NEXT package runs through this module so one command produces a result
manifest tied to one release candidate, one immutable configuration snapshot
and one owned run directory.  The harness:

* refuses to create or touch anything outside ``tmp_test/next``;
* registers every temporary directory before it is used, so a crashed run can
  be identified from its ownership marker instead of a directory-name prefix;
* treats a zero-case or skipped pytest phase as a failure (through
  ``scripts.qa_no_skip``) and validates the JUnit report itself;
* keeps evidence when a phase fails, removes only owned temporary resources,
  and reports a refused cleanup instead of swallowing it;
* never drops a database, kills a process or stops a port: those identities are
  reported to the operator as untouched.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
NEXT_ROOT = Path(os.environ.get("NEXT_HARNESS_ROOT") or (ROOT / "tmp_test" / "next"))
SCHEMA = "next-run-v1"
OWNERSHIP_SCHEMA = "next-ownership-v1"
RESOURCE_SCHEMA = "next-resources-v1"
TASK_PATTERN = re.compile(r"^next\d{2}$")
PHASE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
CLEANABLE = {"temp_dir", "file"}
RESOURCE_KINDS = {"temp_dir", "file", "evidence_dir", "port", "database", "process"}
SENSITIVE_ENV = {"DATABASE_URL", "PGPASSWORD", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_DB_URL"}
PROFILE_DIR = Path(__file__).with_name("next_profiles")


class HarnessError(RuntimeError):
    def __init__(self, code: str, reason: str, next_action: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.next_action = next_action


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def candidate_fingerprint(root: Path | None = None) -> dict:
    from scripts.release_candidate import candidate_fingerprint as fingerprint

    return fingerprint(Path(root or ROOT))


def _secure(path: Path) -> None:
    """Owner-only permissions; icacls avoids a PowerShell start per file."""
    path = Path(path)
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
    if not user:
        raise HarnessError(
            "acl_unavailable",
            "cannot determine the current Windows user",
            "run from an interactive user session",
        )
    result = subprocess.run(
        ["icacls", str(path.resolve()), "/inheritance:r", "/grant:r", f"{user}:F"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise HarnessError(
            "acl_unavailable",
            f"cannot protect {path.name}: {result.stderr.strip()[:200]}",
            "verify the filesystem supports NTFS ACLs",
        )


def _private_write(path: Path, raw: bytes) -> None:
    path = Path(path)
    with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    _secure(path)


def _write_private_json(path: Path, payload) -> None:
    _private_write(path, _canonical(payload))


def _replace_json(path: Path, payload) -> None:
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    _private_write(temporary, _canonical(payload))
    os.replace(temporary, path)


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _remove_tree(path: Path) -> None:
    path = Path(path)
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except PermissionError:
        # Windows read-only files survive rmtree; clear the flag once and retry.
        for child in path.rglob("*"):
            try:
                child.chmod(0o700)
            except OSError:
                pass
        shutil.rmtree(path)


def _process_info(pid: int) -> tuple[bool, str | None]:
    """Return (alive, creation token). A terminated Windows process can stay
    openable while the parent still holds its handle, so the exit code decides."""
    if pid <= 0:
        return False, None
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False, None
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False, None
            if code.value != 259:  # STILL_ACTIVE
                return False, None
            creation = ctypes.c_ulonglong()
            exited = ctypes.c_ulonglong()
            kernel = ctypes.c_ulonglong()
            user = ctypes.c_ulonglong()
            ok = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            return (True, str(creation.value)) if ok else (True, None)
        finally:
            kernel32.CloseHandle(handle)
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return True, fields[21]
    except (OSError, IndexError):
        return False, None


def _process_start_token(pid: int) -> str | None:
    return _process_info(pid)[1]


def _process_alive(pid: int, expected_token: str | None) -> bool:
    alive, token = _process_info(pid)
    if not alive:
        return False
    if expected_token and token and token != expected_token:
        # The PID was reused by an unrelated process: never treat it as ours.
        return False
    return True


def _resolve_under_root(
    run_dir: Path, base: Path | None = None, *, must_exist: bool = False
) -> Path:
    root = Path(base or NEXT_ROOT).resolve()
    candidate = Path(run_dir)
    if ".." in candidate.parts:
        raise HarnessError(
            "invalid_run_dir",
            "run directories may not contain '..'",
            "pass a direct child of tmp_test/next",
        )
    resolved = candidate.resolve()
    if resolved == root or root not in resolved.parents:
        raise HarnessError(
            "path_outside_root",
            f"{resolved} is outside {root}",
            "keep every NEXT run under tmp_test/next",
        )
    if resolved.parent != root:
        raise HarnessError(
            "invalid_run_dir",
            "run directories must be direct children of tmp_test/next",
            "use tmp_test/next/<task>-<run-id>",
        )
    if must_exist and not resolved.exists():
        raise HarnessError("run_missing", f"{resolved} does not exist", "check the run id")
    return resolved


def _load_ownership(run_dir: Path) -> dict:
    path = Path(run_dir) / "ownership.json"
    if not path.is_file():
        raise HarnessError(
            "not_owned",
            f"{Path(run_dir).name} has no ownership marker",
            "leave foreign directories untouched",
        )
    try:
        payload = _read_json(path)
    except (OSError, ValueError) as error:
        raise HarnessError(
            "not_owned", f"ownership marker is unreadable: {error}", "leave the directory untouched"
        ) from error
    if payload.get("schema") != OWNERSHIP_SCHEMA:
        raise HarnessError(
            "not_owned", "ownership marker schema is not NEXT", "leave the directory untouched"
        )
    if not TASK_PATTERN.match(str(payload.get("task", ""))):
        raise HarnessError(
            "not_owned", "ownership marker names another task", "leave the directory untouched"
        )
    if payload.get("run_id") != Path(run_dir).name:
        raise HarnessError(
            "not_owned", "ownership marker names another run", "leave the directory untouched"
        )
    return payload


def _redaction_values(config: dict) -> list[str]:
    values: list[str] = []

    def walk(value):
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(config)
    unique = []
    for value in values:
        if len(value) >= 6 and value not in unique:
            unique.append(value)
    return unique


def _redact(text: str, values: list[str]) -> str:
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "<redacted>")
    return text


def _config_snapshot(run_dir: Path, config_paths) -> dict:
    entry: list[dict] = []
    digests: dict[str, str] = {}
    private = run_dir / "config-private"
    if config_paths:
        private.mkdir()
        _secure(private)
    for source in config_paths:
        source = Path(source).resolve()
        if not source.is_file():
            raise HarnessError(
                "config_missing",
                f"configuration {source.name} does not exist",
                "pass a readable configuration file",
            )
        raw = source.read_bytes()
        target = private / source.name
        if target.exists():
            raise HarnessError(
                "config_duplicate",
                f"two configurations share the name {source.name}",
                "rename one configuration file",
            )
        _private_write(target, raw)
        digest = _sha256_bytes(raw)
        digests[source.name] = digest
        try:
            parsed = json.loads(raw)
            redactable = _redaction_values(parsed)
        except ValueError:
            redactable = []
        entry.append(
            {
                "name": source.name,
                "sha256": digest,
                "source": str(source),
                "frozen": str(target.relative_to(run_dir)),
                "redactable": redactable,
            }
        )
    return {
        "files": entry,
        "digest": _sha256_bytes(_canonical(digests)),
        "redactable": [value for item in entry for value in item["redactable"]],
    }


def create_run(
    task: str, run_dir: Path, *, config_paths=(), pid: int | None = None, base: Path | None = None
) -> Path:
    if not TASK_PATTERN.match(task):
        raise HarnessError("invalid_task", f"{task!r} is not a NEXT task id", "use next01..next11")
    run_dir = _resolve_under_root(run_dir, base)
    if run_dir.exists():
        raise HarnessError(
            "run_exists",
            f"{run_dir} already exists",
            "choose a new run id; never reuse a run directory",
        )
    owner_pid = int(pid if pid is not None else os.getpid())
    try:
        run_dir.parent.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(mode=0o700)
        _secure(run_dir)
        ownership = {
            "schema": OWNERSHIP_SCHEMA,
            "task": task,
            "run_id": run_dir.name,
            "state": "initializing",
            "pid": owner_pid,
            "pid_start": _process_start_token(owner_pid),
            "created_at": _now(),
            "token": secrets.token_hex(16),
            "candidate": candidate_fingerprint(),
        }
        _write_private_json(run_dir / "ownership.json", ownership)
        _write_private_json(
            run_dir / "resources.json", {"schema": RESOURCE_SCHEMA, "resources": []}
        )
        config = _config_snapshot(run_dir, config_paths)
        (run_dir / "evidence").mkdir()
        ownership["state"] = "running"
        ownership["config"] = {item["name"]: item["sha256"] for item in config["files"]}
        ownership["config_sources"] = {item["name"]: item["source"] for item in config["files"]}
        _replace_json(run_dir / "ownership.json", ownership)
    except BaseException:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    return run_dir


def _load_resources(run_dir: Path) -> dict:
    payload = _read_json(Path(run_dir) / "resources.json")
    if payload.get("schema") != RESOURCE_SCHEMA:
        raise HarnessError(
            "not_owned", "resource ledger schema is not NEXT", "leave the directory untouched"
        )
    return payload


def register_resource(
    run_dir: Path, kind: str, *, path: Path | None = None, identity: str | None = None
) -> dict:
    run_dir = Path(run_dir).resolve()
    _load_ownership(run_dir)
    if kind not in RESOURCE_KINDS:
        raise HarnessError(
            "invalid_resource",
            f"unknown resource kind {kind!r}",
            "use a declared NEXT resource kind",
        )
    entry: dict = {
        "id": "",
        "kind": kind,
        "state": "active",
        "created_at": _now(),
        "pid": os.getpid(),
    }
    if kind in CLEANABLE or kind == "evidence_dir":
        if path is None:
            raise HarnessError("invalid_resource", f"{kind} requires a path", "pass the owned path")
        resolved = Path(path).resolve()
        if resolved == run_dir or run_dir not in resolved.parents:
            raise HarnessError(
                "path_outside_run",
                f"{resolved} is outside the run directory",
                "only register paths this run created",
            )
        entry["path"] = str(resolved.relative_to(run_dir))
        if kind == "temp_dir":
            resolved.mkdir(parents=True, exist_ok=True)
    else:
        if not identity:
            raise HarnessError(
                "invalid_resource",
                f"{kind} requires an identity",
                "record the port, database or process identity",
            )
        entry["identity"] = identity
    ledger = _load_resources(run_dir)
    existing = [
        item
        for item in ledger["resources"]
        if item.get("identity") == entry.get("identity")
        and item.get("path") == entry.get("path")
        and item["kind"] == kind
    ]
    if existing:
        return existing[0]
    entry["id"] = f"r{len(ledger['resources']) + 1:03d}"
    ledger["resources"].append(entry)
    _replace_json(run_dir / "resources.json", ledger)
    return entry


@dataclass(frozen=True)
class Phase:
    name: str
    command: list[str]
    kind: str = "command"
    required: bool = True
    timeout: int = 900
    artifact: str | None = None

    def validate(self) -> None:
        if not PHASE_PATTERN.match(self.name):
            raise HarnessError(
                "invalid_phase",
                f"phase name {self.name!r} is not a safe file stem",
                "use lowercase letters, digits, '-' or '_'",
            )
        if self.kind not in {"pytest", "command"}:
            raise HarnessError(
                "invalid_phase", f"phase kind {self.kind!r} is unknown", "use pytest or command"
            )
        if not self.command:
            raise HarnessError(
                "invalid_phase", f"phase {self.name} has no command", "declare the command list"
            )


def _phase_env(run_dir: Path) -> dict:
    env = {key: value for key, value in os.environ.items() if key not in SENSITIVE_ENV}
    env["PYTHONPATH"] = str(ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["NEXT_RUN_DIR"] = str(run_dir)
    env.pop("PYTEST_ADDOPTS", None)
    return env


def _junit_summary(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as error:
        raise HarnessError(
            "invalid_junit",
            f"JUnit report is unreadable: {error}",
            "rerun the phase; a corrupt report is not evidence",
        ) from error
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        suites = root.findall("testsuite")
        if not suites:
            return None
        suite = suites[0]
    cases = root.iter("testcase")
    total = failures = errors = skipped = 0
    for case in cases:
        total += 1
        failures += len(case.findall("failure"))
        errors += len(case.findall("error"))
        skipped += len(case.findall("skipped"))
    return {"tests": total, "failures": failures, "errors": errors, "skipped": skipped}


def _run_one(run_dir: Path, phase: Phase, redactable: list[str]) -> dict:
    phase.validate()
    started = time.monotonic()
    evidence = run_dir / "evidence"
    command = list(phase.command)
    junit_path = None
    if phase.kind == "pytest":
        junit_path = evidence / f"{phase.name}.xml"
        basetemp = run_dir / "tmp" / phase.name
        basetemp.mkdir(parents=True, exist_ok=True)
        register_resource(run_dir, "temp_dir", path=basetemp)
        command = command + [
            "-p",
            "scripts.qa_no_skip",
            "-q",
            f"--junitxml={junit_path}",
            f"--basetemp={basetemp}",
        ]
    result = {
        "name": phase.name,
        "kind": phase.kind,
        "status": "running",
        "exit_code": None,
        "seconds": None,
        "tests": None,
        "failures": None,
        "errors": None,
        "skipped": None,
        "junit": junit_path.name if junit_path else None,
    }
    diagnostic: dict | None = None
    stdout = stderr = ""
    try:
        completed = subprocess.run(
            command,
            cwd=str(ROOT),
            env=_phase_env(run_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=phase.timeout,
        )
        stdout, stderr = completed.stdout, completed.stderr
        result["exit_code"] = completed.returncode
    except subprocess.TimeoutExpired as error:
        stdout = (error.stdout or "") if isinstance(error.stdout, str) else ""
        stderr = (error.stderr or "") if isinstance(error.stderr, str) else ""
        result["exit_code"] = None
        diagnostic = {
            "code": "phase_timeout",
            "reason": f"phase exceeded {phase.timeout}s",
            "next_action": "inspect the log and rerun with a measured timeout; a timeout is not a pass",
        }
    except OSError as error:
        result["exit_code"] = None
        diagnostic = {
            "code": "phase_not_started",
            "reason": str(error)[:300],
            "next_action": "fix the command or environment, then rerun the phase",
        }
    result["seconds"] = round(time.monotonic() - started, 3)

    log = evidence / f"{phase.name}.log"
    body = (
        f"$ {' '.join(command)}\n"
        f"exit_code={result['exit_code']}\n"
        f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n"
    )
    _private_write(log, _redact(body, redactable).encode("utf-8"))
    result["log"] = log.name
    result["log_sha256"] = _sha256_file(log)

    summary = None
    if junit_path is not None:
        try:
            summary = _junit_summary(junit_path)
        except HarnessError as error:
            diagnostic = {
                "code": error.code,
                "reason": error.reason,
                "next_action": error.next_action,
            }
        if summary is not None:
            result.update(summary)
    if diagnostic is None:
        if junit_path is not None and summary is None:
            diagnostic = {
                "code": "no_junit",
                "reason": "pytest produced no JUnit report",
                "next_action": "check the pytest command; missing evidence is not a pass",
            }
        elif summary is not None and summary["tests"] == 0:
            diagnostic = {
                "code": "no_test_cases",
                "reason": "the phase collected zero test cases",
                "next_action": "fix the selection; zero cases never counts as a pass",
            }
        elif summary is not None and summary["skipped"]:
            diagnostic = {
                "code": "skipped_cases",
                "reason": f"{summary['skipped']} test cases were skipped",
                "next_action": "remove the skip or provide the missing dependency; skips are not accepted",
            }
        elif summary is not None and (summary["failures"] or summary["errors"]):
            diagnostic = {
                "code": "failing_cases",
                "reason": f"{summary['failures']} failures and {summary['errors']} errors",
                "next_action": "read the log, fix the defect, rerun the phase",
            }
        elif result["exit_code"] != 0:
            diagnostic = {
                "code": "phase_failed",
                "reason": f"exit code {result['exit_code']}",
                "next_action": "read the log; the phase did not pass",
            }
    if diagnostic:
        diagnostic["reason"] = _redact(diagnostic["reason"], redactable)
        result["diagnostic"] = diagnostic
        result["status"] = "failed"
    else:
        result["status"] = "passed"
    return result


def _evidence_index(run_dir: Path) -> list[dict]:
    items = []
    for path in sorted((run_dir / "evidence").iterdir()):
        if path.is_file():
            items.append(
                {"file": path.name, "bytes": path.stat().st_size, "sha256": _sha256_file(path)}
            )
    return items


def _cleanup(run_dir: Path, redactable: list[str]) -> dict:
    ledger = _load_resources(run_dir)
    removed, missing, failures, untouched = [], [], [], []
    for item in ledger["resources"]:
        if item["kind"] not in CLEANABLE:
            untouched.append(
                {"id": item["id"], "kind": item["kind"], "identity": item.get("identity")}
            )
            continue
        if item.get("state") in {"removed", "missing"}:
            continue
        target = (run_dir / item["path"]).resolve()
        if run_dir not in target.parents:
            failures.append(
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "code": "path_outside_run",
                    "reason": "refused to delete a path outside the run",
                    "next_action": "inspect the ledger manually",
                }
            )
            item["state"] = "blocked"
            continue
        if not target.exists():
            item["state"] = "missing"
            missing.append(item["id"])
            continue
        try:
            _remove_tree(target)
            item["state"] = "removed"
            removed.append(item["id"])
        except OSError as error:
            item["state"] = "blocked"
            reason = _redact(str(error)[:300], redactable)
            failures.append(
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "code": "cleanup_denied",
                    "reason": reason,
                    "next_action": "close the process holding the file, then rerun recovery",
                }
            )
    _replace_json(run_dir / "resources.json", ledger)
    return {
        "status": "failed" if failures else "passed",
        "removed": removed,
        "missing": missing,
        "failures": failures,
        "untouched": untouched,
    }


def _config_from_ownership(run_dir: Path, ownership: dict) -> dict:
    files = []
    sources = ownership.get("config_sources", {})
    for name, digest in sorted(ownership.get("config", {}).items()):
        frozen = run_dir / "config-private" / name
        files.append(
            {
                "name": name,
                "sha256": digest,
                "source": sources.get(name, str(frozen)),
                "frozen": str(frozen.relative_to(run_dir)),
            }
        )
    redactable: list[str] = []
    for item in files:
        try:
            redactable.extend(
                _redaction_values(
                    json.loads((run_dir / "config-private" / item["name"]).read_bytes())
                )
            )
        except (OSError, ValueError):
            pass
    return {
        "files": files,
        "digest": _sha256_bytes(_canonical({item["name"]: item["sha256"] for item in files})),
        "redactable": redactable,
    }


def run_phases(run_dir: Path, phases: list[Phase], *, base: Path | None = None) -> dict:
    run_dir = _resolve_under_root(run_dir, base, must_exist=True)
    ownership = _load_ownership(run_dir)
    if ownership.get("state") not in {"initializing", "running"}:
        raise HarnessError(
            "run_closed",
            f"run is already {ownership.get('state')}",
            "create a new run instead of rewriting evidence",
        )
    config = _config_from_ownership(run_dir, ownership)
    redactable = config["redactable"]
    candidate_before = ownership["candidate"]
    current = candidate_fingerprint()
    diagnostics: list[dict] = []
    started_at = _now()
    results: dict[str, dict] = {}

    if current != candidate_before:
        diagnostics.append(
            {
                "code": "candidate_changed",
                "reason": "candidate changed before the first phase",
                "next_action": "start a new run on the current candidate",
            }
        )

    stopped = bool(diagnostics)
    for phase in phases:
        if stopped:
            results[phase.name] = {
                "name": phase.name,
                "kind": phase.kind,
                "status": "not_run",
                "diagnostic": {
                    "code": "blocked_by_failure",
                    "reason": "an earlier phase failed",
                    "next_action": "fix the first failure and start a new run",
                },
            }
            continue
        result = _run_one(run_dir, phase, redactable)
        results[phase.name] = result
        if result["status"] != "passed" and phase.required:
            stopped = True

    candidate_after = candidate_fingerprint()
    stable = candidate_after == candidate_before
    if not stable:
        diagnostics.append(
            {
                "code": "candidate_changed",
                "reason": "candidate changed while the run was executing",
                "next_action": "freeze the worktree and start a new run; this evidence is void",
            }
        )

    cleanup = _cleanup(run_dir, redactable)
    failed = any(item["status"] == "failed" for item in results.values()) or bool(diagnostics)
    if failed:
        status = "failed"
    elif cleanup["status"] != "passed":
        status = "failed_cleanup"
    else:
        status = "passed"

    manifest = {
        "schema": SCHEMA,
        "task": ownership["task"],
        "run_id": ownership["run_id"],
        "status": status,
        "started_at": started_at,
        "finished_at": _now(),
        "candidate": {"before": candidate_before, "after": candidate_after, "stable": stable},
        "config": {"files": config["files"], "digest": config["digest"]},
        "phases": results,
        "diagnostics": diagnostics,
        "cleanup": cleanup,
        "evidence": _evidence_index(run_dir),
        "host": {"os": os.name, "python": sys.version.split()[0], "pid": os.getpid()},
        "ownership_token_sha256": _sha256_bytes(ownership["token"].encode("utf-8")),
    }
    _replace_json(run_dir / "manifest.json", manifest)
    ownership["state"] = {
        "passed": "completed",
        "failed": "failed",
        "failed_cleanup": "failed_cleanup",
    }[status]
    ownership["finished_at"] = manifest["finished_at"]
    _replace_json(run_dir / "ownership.json", ownership)
    return manifest


def status(base: Path | None = None) -> list[dict]:
    root = Path(base or NEXT_ROOT)
    if not root.exists():
        return []
    report = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        entry = {"run_id": child.name, "path": str(child)}
        try:
            ownership = _load_ownership(child)
        except HarnessError as error:
            entry.update({"state": "foreign", "reason": error.reason})
            report.append(entry)
            continue
        entry.update(
            {
                "task": ownership["task"],
                "state": ownership.get("state"),
                "pid": ownership.get("pid"),
                "started_at": ownership.get("created_at"),
                "candidate_head": ownership.get("candidate", {}).get("head"),
            }
        )
        manifest_path = child / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = _read_json(manifest_path)
                entry["manifest_status"] = manifest.get("status")
                entry["cleanup_status"] = manifest.get("cleanup", {}).get("status")
            except ValueError:
                entry["manifest_status"] = "unreadable"
        if ownership.get("state") in {
            "completed",
            "failed",
            "failed_cleanup",
            "recovered",
            "recovery_failed",
        }:
            entry["state"] = ownership["state"]
        elif _process_alive(int(ownership.get("pid", 0)), ownership.get("pid_start")):
            entry["state"] = "running"
        else:
            entry["state"] = "stale"
        try:
            entry["resources"] = _load_resources(child)["resources"]
        except (OSError, ValueError, HarnessError):
            entry["resources"] = []
        report.append(entry)
    return report


def recover(run_dir: Path, *, base: Path | None = None) -> dict:
    run_dir = _resolve_under_root(run_dir, base, must_exist=True)
    ownership = _load_ownership(run_dir)
    if ownership.get("state") == "recovered":
        return {
            "status": "already_recovered",
            "run_id": run_dir.name,
            "removed": [],
            "untouched": [],
        }
    if _process_alive(int(ownership.get("pid", 0)), ownership.get("pid_start")):
        raise HarnessError(
            "run_active",
            f"{run_dir.name} is still owned by a live process",
            "stop the runner through its own lifecycle; never delete a live run",
        )
    config = _config_from_ownership(run_dir, ownership)
    redactable = config["redactable"]
    ledger = _load_resources(run_dir)
    removed, missing, failures, untouched = [], [], [], []
    for item in ledger["resources"]:
        if item["kind"] not in CLEANABLE:
            untouched.append(
                {"id": item["id"], "kind": item["kind"], "identity": item.get("identity")}
            )
            continue
        target = (run_dir / item["path"]).resolve()
        if run_dir not in target.parents:
            failures.append(
                {
                    "id": item["id"],
                    "code": "path_outside_run",
                    "reason": "refused to delete a path outside the run",
                    "next_action": "inspect the ledger manually",
                }
            )
            continue
        if not target.exists():
            item["state"] = "missing"
            missing.append(item["id"])
            continue
        try:
            _remove_tree(target)
            item["state"] = "removed"
            removed.append(item["id"])
        except OSError as error:
            item["state"] = "blocked"
            failures.append(
                {
                    "id": item["id"],
                    "code": "cleanup_denied",
                    "reason": _redact(str(error)[:300], redactable),
                    "next_action": "close the process holding the file, then rerun recovery",
                }
            )
    _replace_json(run_dir / "resources.json", ledger)
    result = {
        "schema": "next-recovery-v1",
        "run_id": run_dir.name,
        "status": "recovered" if not failures else "failed_cleanup",
        "recovered_at": _now(),
        "removed": removed,
        "missing": missing,
        "failures": failures,
        "untouched": untouched,
        "operator": os.environ.get("USERNAME") or os.environ.get("USER"),
    }
    _write_private_json(run_dir / "recovery.json", result)
    ownership["state"] = "recovered" if not failures else "recovery_failed"
    _replace_json(run_dir / "ownership.json", ownership)
    return result


def verify(run_dir: Path, *, base: Path | None = None) -> dict:
    run_dir = _resolve_under_root(run_dir, base, must_exist=True)
    ownership = _load_ownership(run_dir)
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        return {
            "valid": False,
            "run_id": run_dir.name,
            "reasons": [{"code": "manifest_missing", "reason": "the run has no manifest"}],
        }
    manifest = _read_json(manifest_path)
    reasons = []
    if manifest.get("schema") != SCHEMA:
        reasons.append({"code": "schema_mismatch", "reason": "manifest schema is not NEXT"})
    if manifest.get("ownership_token_sha256") != _sha256_bytes(ownership["token"].encode("utf-8")):
        reasons.append(
            {
                "code": "ownership_mismatch",
                "reason": "manifest does not belong to this ownership marker",
            }
        )
    current = candidate_fingerprint()
    if current != manifest.get("candidate", {}).get("after"):
        reasons.append(
            {
                "code": "candidate_changed",
                "reason": "the worktree no longer matches the recorded candidate",
            }
        )
    for item in manifest.get("config", {}).get("files", []):
        frozen = run_dir / item.get("frozen", "")
        if not frozen.is_file() or _sha256_file(frozen) != item["sha256"]:
            reasons.append(
                {
                    "code": "config_tampered",
                    "reason": f"frozen configuration {item['name']} changed",
                }
            )
    source = ownership.get("config", {})
    for name, digest in source.items():
        recorded = next(
            (item for item in manifest.get("config", {}).get("files", []) if item["name"] == name),
            None,
        )
        if recorded is None:
            continue
        candidate_source = Path(recorded.get("source", ""))
        if candidate_source.is_file() and _sha256_file(candidate_source) != digest:
            reasons.append(
                {
                    "code": "config_changed",
                    "reason": f"configuration source {name} changed after the run",
                }
            )
    for item in manifest.get("evidence", []):
        path = run_dir / "evidence" / item["file"]
        if not path.is_file() or _sha256_file(path) != item["sha256"]:
            reasons.append(
                {"code": "evidence_changed", "reason": f"evidence {item['file']} changed"}
            )
    return {
        "valid": not reasons,
        "run_id": run_dir.name,
        "status": manifest.get("status"),
        "reasons": reasons,
    }


def load_profile(path: Path) -> list[Phase]:
    payload = _read_json(path)
    if not isinstance(payload.get("phases"), list) or not payload["phases"]:
        raise HarnessError(
            "invalid_profile", f"{Path(path).name} has no phases", "declare at least one phase"
        )
    return [Phase(**item) for item in payload["phases"]]


def _default_profile(task: str) -> Path | None:
    candidate = PROFILE_DIR / f"{task}.json"
    return candidate if candidate.is_file() else None


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NEXT candidate-bound acceptance runner")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="execute phases and write a manifest")
    run_parser.add_argument("--task", required=True)
    run_parser.add_argument("--run-dir", required=True)
    run_parser.add_argument("--profile")
    run_parser.add_argument("--config", action="append", default=[])

    status_parser = sub.add_parser("status", help="inspect known runs")
    status_parser.add_argument("--base")

    recover_parser = sub.add_parser(
        "recover", help="dispose a stale run's owned temporary resources"
    )
    recover_parser.add_argument("--run-dir", required=True)

    verify_parser = sub.add_parser("verify", help="revalidate a finished run")
    verify_parser.add_argument("--run-dir", required=True)

    hold_parser = sub.add_parser("hold", help=argparse.SUPPRESS)
    hold_parser.add_argument("--run-dir", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            profile = Path(args.profile) if args.profile else _default_profile(args.task)
            if profile is None:
                raise HarnessError(
                    "profile_missing", f"no profile for {args.task}", "pass --profile"
                )
            phases = load_profile(profile)
            run_dir = create_run(
                args.task, Path(args.run_dir), config_paths=[Path(item) for item in args.config]
            )
            manifest = run_phases(run_dir, phases)
            _print(
                {
                    "run_id": manifest["run_id"],
                    "status": manifest["status"],
                    "manifest": str(run_dir / "manifest.json"),
                    "phases": {name: item["status"] for name, item in manifest["phases"].items()},
                    "cleanup": manifest["cleanup"]["status"],
                }
            )
            return 0 if manifest["status"] == "passed" else 1
        if args.command == "status":
            _print(status(Path(args.base) if args.base else None))
            return 0
        if args.command == "recover":
            result = recover(Path(args.run_dir))
            _print(result)
            return 0 if result["status"] in {"recovered", "already_recovered"} else 1
        if args.command == "verify":
            result = verify(Path(args.run_dir))
            _print(result)
            return 0 if result["valid"] else 1
        if args.command == "hold":
            run_dir = create_run("next01", Path(args.run_dir))
            register_resource(run_dir, "temp_dir", path=run_dir / "scratch")
            _write_private_json(run_dir / "ready.json", {"pid": os.getpid(), "created_at": _now()})
            time.sleep(300)
            return 0
    except HarnessError as error:
        _print({"error": error.code, "reason": error.reason, "next_action": error.next_action})
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
