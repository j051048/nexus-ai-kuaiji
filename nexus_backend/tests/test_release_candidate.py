"""Release evidence must cover the entire candidate, not only backend code."""

import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest

from scripts import release_candidate as candidate
from scripts import n02_n03_postgres_gate as pg_gate


def git(root, *args):
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Offline fixture")
    git(root, "config", "user.email", "fixture@example.test")
    for name, content in {
        "src/App.tsx": "export const version = 1;\n",
        "nexus_backend/app/main.py": "VERSION = 1\n",
        "nexus_backend/migrations/postgres/001_fixture.sql": "SELECT 1;\n",
        "package-lock.json": '{"lockfileVersion":3}\n',
        "vercel.json": '{"version":2}\n',
        ".github/workflows/accounting.yml": "name: fixture\n",
        ".gitignore": "tmp_test/\n.env\n",
    }.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-qm", "synthetic candidate")
    monkeypatch.setattr(candidate, "ROOT", root)
    return root


@pytest.mark.parametrize("path", ["src/App.tsx", "package-lock.json", "vercel.json"])
def test_frontend_dependency_and_deployment_changes_invalidate_candidate(repository, path):
    before = candidate.candidate_fingerprint()
    target = repository / path
    target.write_text(target.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    after = candidate.candidate_fingerprint()
    assert before["source_sha256"] != after["source_sha256"]


@pytest.fixture
def evidence(repository):
    manifest = repository / "scripts/n02_n03_postgres_required_cases.json"
    manifest.parent.mkdir()
    manifest.write_bytes(pg_gate.REQUIRED_PATH.read_bytes())
    git(repository, "add", ".")
    git(repository, "commit", "-qm", "frozen test contract")
    fingerprint = candidate.candidate_fingerprint(repository)
    directories = {}
    for major in (16, 17):
        folder = repository / "tmp_test" / str(major)
        folder.mkdir(parents=True)
        suites = {}
        for suite, spec in pg_gate.load_contract()["suites"].items():
            node = ET.Element(
                "testsuite", tests=str(len(spec["cases"])), skipped="0", errors="0", failures="0"
            )
            for identity in spec["cases"]:
                classname, name = identity.split("::", 1)
                ET.SubElement(node, "testcase", classname=classname, name=name)
            path = folder / f"{suite}.xml"
            ET.ElementTree(node).write(path, encoding="utf-8", xml_declaration=True)
            suites[suite] = {"status": "passed", **pg_gate.validate_junit(path, suite)}
        summary = {
            "schema": "n02-n03-pg-gate-v1",
            "status": "passed",
            "major": major,
            "candidate": fingerprint,
            "suites": suites,
            "required_cases_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "server_version_num": major * 10000 + 15,
            "durability": dict.fromkeys(pg_gate.DURABILITY, "on"),
            "roles_checked": True,
            "migrations": {
                "count": 1,
                "digest": fingerprint["migration_sha256"],
                "status": "verified",
            },
        }
        (folder / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
        directories[major] = folder
    return repository, directories


def check(evidence, **kwargs):
    root, directories = evidence
    checker = importlib.import_module("scripts.check_release_candidate")
    return checker.check_candidate(directories[16], directories[17], root=root, **kwargs)


def change_summary(folder, action):
    path = folder / "summary.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    action(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_full_offline_evidence_only_grants_technical_readiness(evidence):
    result = check(evidence)
    assert result["status"] == "passed"
    assert result["readiness"] == "n02_n03_technical_passed"
    assert result["scope"] == "n02_n03_only"
    assert result["deployment_authorized"] is False
    assert result["remote_state_verified"] is False
    assert result["worktree_clean"] is True


@pytest.mark.parametrize("field", ["head", "source_sha256", "migration_sha256"])
def test_two_pg_versions_must_bind_the_same_complete_candidate(evidence, field):
    change_summary(
        evidence[1][17],
        lambda value: value["candidate"].update({field: "0" * len(value["candidate"][field])}),
    )
    assert check(evidence)["status"] == "blocked"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(status="failed"),
        lambda value: value.update(major=17),
        lambda value: value.update(server_version_num=140024),
        lambda value: value.update(roles_checked=False),
        lambda value: value["durability"].update(fsync="off"),
        lambda value: value.update(required_cases_sha256="0" * 64),
        lambda value: value["migrations"].update(digest="0" * 64),
        lambda value: value["migrations"].update(status="applied"),
        lambda value: value["migrations"].update(count=True),
        lambda value: value["suites"]["n02"].update(passed=True),
        lambda value: value["suites"]["n02"].update(passed=999),
        lambda value: value["suites"]["n02"].update(status="skipped"),
        lambda value: value["suites"].pop("n03"),
    ],
)
def test_untrusted_summary_cannot_claim_pass(evidence, mutate):
    change_summary(evidence[1][16], mutate)
    assert check(evidence)["status"] == "blocked"


@pytest.mark.parametrize(
    "malformation",
    ["skipped", "failure", "error", "count", "duplicate", "substituted", "missing", "bad_xml"],
)
def test_raw_junit_is_revalidated_even_when_hash_and_summary_claim_pass(evidence, malformation):
    path = evidence[1][16] / "n02.xml"
    tree = ET.parse(path)
    node = tree.getroot()
    if malformation in {"skipped", "failure", "error"}:
        ET.SubElement(node.find("testcase"), malformation)
    elif malformation == "count":
        node.set("tests", "500")
    elif malformation == "duplicate":
        node[-1].attrib.update(node[0].attrib)
    elif malformation == "substituted":
        node[-1].set("name", "test_not_executed")
    elif malformation == "missing":
        node.remove(node[-1])
        node.set("tests", str(len(node)))
    tree.write(path, encoding="utf-8")
    if malformation == "bad_xml":
        path.write_text("<broken", encoding="utf-8")
    change_summary(
        evidence[1][16],
        lambda value: value["suites"]["n02"].update(
            junit_sha256=hashlib.sha256(path.read_bytes()).hexdigest()
        ),
    )
    assert check(evidence)["status"] == "blocked"


@pytest.mark.parametrize(
    "path",
    [
        "src/App.tsx",
        "scripts/n02_n03_postgres_required_cases.json",
        "nexus_backend/migrations/postgres/001_fixture.sql",
    ],
)
def test_source_changes_after_pg_runs_invalidate_evidence(evidence, path):
    target = evidence[0] / path
    target.write_bytes(target.read_bytes() + b"\n")
    assert check(evidence)["status"] == "blocked"


def test_missing_pg_or_missing_raw_junit_blocks(evidence):
    (evidence[1][17] / "n03.xml").unlink()
    assert check(evidence)["status"] == "blocked"


def test_duplicate_json_keys_are_rejected(evidence):
    path = evidence[1][16] / "summary.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"status": "passed"', '"status": "failed", "status": "passed"', 1
        ),
        encoding="utf-8",
    )
    assert check(evidence)["status"] == "blocked"


def test_dirty_worktree_can_pass_technical_but_never_release(evidence):
    (evidence[0] / "notes.md").write_text("uncommitted review notes", encoding="utf-8")
    technical = check(evidence)
    assert technical["status"] == "passed"
    assert technical["worktree_clean"] is False
    assert check(evidence, mode="release")["status"] == "blocked"


def test_release_requires_local_plan_and_does_not_infer_platform_state(evidence):
    result = check(evidence, mode="release")
    assert result["status"] == "blocked"
    assert result["deployment_authorized"] is False
    assert result["remote_state_verified"] is False


@pytest.fixture
def release_plan(evidence):
    current = candidate.candidate_fingerprint(evidence[0])
    plan = {
        "schema": "controlled-release-plan-v1",
        "candidate": current,
        "reviewed_by": "synthetic reviewer",
        "review_reference": "offline-review-1",
        "platform_checks": {
            platform: {"auto_deploy": "disabled", "evidence_reference": "manual-record-only"}
            for platform in ("vercel", "zeabur")
        },
        "migration": {
            "target_digest": current["migration_sha256"],
            "prior_digest": "a" * 64,
            "backup_reference": "offline-backup-1",
            "compatibility_review_reference": "api-worker-forward-backward-review",
        },
        "runtime": {
            service: {"head": current["head"], "artifact_reference": "synthetic-artifact"}
            for service in ("frontend_gateway", "api", "worker", "assistant")
        },
        "rollout_steps": ["Human schedules maintenance and reviews exact candidate"],
        "rollback_steps": ["Human preserves unknown outcomes before rollback"],
        "unknown_results_preserved": True,
    }
    path = evidence[0] / "tmp_test/release-plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_complete_manual_checklist_is_never_online_verification_or_approval(evidence, release_plan):
    result = check(evidence, mode="release", release_plan=release_plan)
    assert result["status"] == "passed"
    assert result["readiness"] == "local_checklist_complete_for_human_review"
    assert result["remote_state_verified"] is False
    assert result["approval_verified"] is False
    assert result["deployment_authorized"] is False


def test_release_without_frontend_gateway_artifact_is_blocked(evidence, release_plan):
    plan = json.loads(release_plan.read_text(encoding="utf-8"))
    plan["runtime"].pop("frontend_gateway")
    release_plan.write_text(json.dumps(plan), encoding="utf-8")
    assert check(evidence, mode="release", release_plan=release_plan)["status"] == "blocked"


@pytest.mark.parametrize(
    "part",
    [
        "frontend_gateway",
        "api",
        "worker",
        "assistant",
        "platform",
        "migration",
        "rollback",
        "unknown",
        "candidate",
    ],
)
def test_release_plan_requires_bound_artifacts_and_recorded_checks(evidence, release_plan, part):
    plan = json.loads(release_plan.read_text(encoding="utf-8"))
    if part in plan["runtime"]:
        plan["runtime"][part]["head"] = "0" * 40
    elif part == "platform":
        plan["platform_checks"]["zeabur"]["auto_deploy"] = "unknown"
    elif part == "migration":
        plan["migration"]["compatibility_review_reference"] = ""
    elif part == "rollback":
        plan["rollback_steps"] = []
    elif part == "unknown":
        plan["unknown_results_preserved"] = False
    else:
        plan["candidate"]["source_sha256"] = "0" * 64
    release_plan.write_text(json.dumps(plan), encoding="utf-8")
    assert check(evidence, mode="release", release_plan=release_plan)["status"] == "blocked"


@pytest.mark.parametrize(
    "path",
    [
        "config/runtime.ts",
        "api/gateway.ts",
        "third_party/runtime.py",
        ".gitattributes",
        ".env.accounting.example",
        "nexus_backend/app/prompt.md",
    ],
)
def test_runtime_configuration_and_prompt_sources_are_part_of_candidate(repository, path):
    before = candidate.candidate_fingerprint()
    target = repository / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("synthetic runtime content\n", encoding="utf-8")
    assert candidate.candidate_fingerprint()["source_sha256"] != before["source_sha256"]


def test_deleting_tracked_source_and_untracked_source_changes_are_bound(repository):
    original = candidate.candidate_fingerprint()
    (repository / "src/App.tsx").unlink()
    deleted = candidate.candidate_fingerprint()
    assert deleted["source_sha256"] != original["source_sha256"]
    (repository / "src/new.tsx").write_text("// new", encoding="utf-8")
    assert candidate.candidate_fingerprint()["source_sha256"] != deleted["source_sha256"]


def test_generated_artifacts_and_private_env_are_not_hashed(repository):
    before = candidate.candidate_fingerprint()
    (repository / "tmp_test").mkdir()
    (repository / "tmp_test/report.json").write_text("{}", encoding="utf-8")
    (repository / ".env").write_text("SYNTHETIC_SECRET=not-real", encoding="utf-8")
    assert candidate.candidate_fingerprint() == before


def test_source_change_while_reading_reports_is_blocked(evidence, monkeypatch):
    checker = importlib.import_module("scripts.check_release_candidate")
    original = checker._report

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (evidence[0] / "src/App.tsx").write_text("// changed during check", encoding="utf-8")
        return result

    monkeypatch.setattr(checker, "_report", changed)
    assert check(evidence)["status"] == "blocked"
