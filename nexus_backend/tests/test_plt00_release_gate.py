from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_acceptance_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "opt_acceptance.py"
    spec = importlib.util.spec_from_file_location("opt_acceptance_plt00", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_requires_configured_pg_and_never_accepts_pg_skip():
    module = _load_acceptance_module()
    results = [
        {
            "module": "OPT01",
            "source": "OPT01 local",
            "command": "local",
            "status": "passed",
            "skipped_tests": 0,
        },
        {
            "module": "OPT02",
            "source": "isolated PostgreSQL acceptance",
            "command": "PG: test_batch_review_postgres.py",
            "status": "skipped",
            "skipped_tests": 0,
            "skip_reason": "no ACCOUNTING_TEST_POSTGRES_URL",
        },
    ]
    blockers = module.release_blockers(results, require_pg=True)
    assert blockers == [
        "OPT02 PG: test_batch_review_postgres.py: skipped without isolated PostgreSQL"
    ]
    assert module.release_blockers(results, require_pg=False) == []


def test_release_rejects_pg_command_that_passed_with_skipped_tests():
    module = _load_acceptance_module()
    results = [
        {
            "module": "OPT03",
            "source": "isolated PostgreSQL acceptance",
            "command": "PG: test_opt03_audit_chain_postgres.py",
            "status": "passed-with-skips",
            "skipped_tests": 3,
        }
    ]
    blockers = module.release_blockers(results, require_pg=True)
    assert blockers == ["OPT03 PG: test_opt03_audit_chain_postgres.py: 3 PostgreSQL tests skipped"]


def test_release_accepts_fully_executed_pg_commands():
    module = _load_acceptance_module()
    results = [
        {
            "module": "OPT04",
            "source": "isolated PostgreSQL acceptance",
            "command": "PG: test_opt04_posting_postgres.py",
            "status": "passed",
            "skipped_tests": 0,
        }
    ]
    assert module.release_blockers(results, require_pg=True) == []


def test_zero_tests_cannot_be_classified_as_passed():
    module = _load_acceptance_module()
    assert module._classify(0, "no tests ran in 0.01s") == "zero-tests"
    assert module._classify(0, "0 tests collected") == "zero-tests"
