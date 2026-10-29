"""The dedicated mandatory gate must replace, never silently omit, PG suites."""

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/accounting.yml"
SPECIALISTS = (
    "test_tax_workpaper_postgres.py",
    "test_n02_tax_concurrency_postgres.py",
    "test_n03_notification_postgres.py",
    "test_n03_worker_postgres.py",
)


def job(name):
    text = WORKFLOW.read_text(encoding="utf-8")
    found = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", text, re.M | re.S)
    assert found, f"Required job missing: {name}"
    return found.group(1)


def test_n02_n03_have_a_required_real_pg16_pg17_job():
    gate = job("n02-n03-postgres-gate")
    assert "postgres: [16, 17]" in gate
    assert "image: postgres:${{ matrix.postgres }}" in gate
    assert "127.0.0.1:55432:5432" in gate
    assert "scripts/ak05_postgres_gate.py prepare" in gate
    assert "--allow-create-local-databases" in gate
    assert "scripts/n02_n03_postgres_gate.py" in gate
    assert "--major ${{ matrix.postgres }}" in gate
    assert "regression.json" in gate
    assert "continue-on-error" not in gate
    assert "|| true" not in gate


def test_legacy_verify_only_delegates_exactly_four_specialist_suites():
    verify = job("verify")
    ignored = re.findall(r"--ignore=nexus_backend/tests/(\S+)", verify)
    assert sorted(ignored) == sorted(SPECIALISTS)
    # An ignore is safe only while the independent strict gate exists.
    assert "scripts/n02_n03_postgres_gate.py" in job("n02-n03-postgres-gate")


def test_only_sanitized_fixed_gate_evidence_is_uploaded():
    gate = job("n02-n03-postgres-gate")
    assert "name: n02-n03-postgres-${{ matrix.postgres }}-evidence" in gate
    assert "if-no-files-found: error" in gate
    assert "path: tmp_test/n02-n03-ci-reports/" in gate
    assert "path: tmp_test/\n" not in gate
    assert "path: tmp_test/n02-n03-ci-config" not in gate


@pytest.mark.parametrize(
    "dependency",
    [
        "verify",
        "e2e",
        "core-regression-gate",
        "ak05-postgres-gate",
        "n02-n03-postgres-gate",
    ],
)
def test_same_candidate_gate_requires_all_ci_predecessors(dependency):
    aggregate = job("controlled-candidate-check")
    needs = re.search(r"^    needs: \[([^]]+)\]", aggregate, re.M)
    assert needs and dependency in [part.strip() for part in needs.group(1).split(",")]
    # A skipped prerequisite must not produce a successful readiness result.
    assert "if: always()" in aggregate
    assert "toJSON(needs)" in aggregate
    assert 'result["result"] != "success"' in aggregate
    assert "check_release_candidate.py" in aggregate
    assert "--pg16" in aggregate and "--pg17" in aggregate
    assert "--mode technical" in aggregate
    assert "continue-on-error" not in aggregate
    assert "vercel deploy" not in aggregate
    assert "zeabur deploy" not in aggregate
