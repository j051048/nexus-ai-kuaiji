"""Run one immutable offline acceptance candidate via existing pytest fixtures."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from .baseline import ROOT, BaselineError, candidate_identity
from tools.ocr_replay import replay

TESTS = [
    "nexus_backend/tests/test_assistant_task_baseline.py",
    "nexus_backend/tests/test_assistant_task_workflow.py",
    "nexus_backend/tests/test_assistant_delivery_context.py",
    "nexus_backend/tests/test_assistant_context_budget.py",
    "nexus_backend/tests/test_assistant_events.py",
    "nexus_backend/tests/test_assistant_investigation.py",
    "nexus_backend/tests/test_ocr_replay_engine.py",
    "nexus_backend/tests/test_ag05_examples.py",
    "nexus_backend/tests/test_ak02_assistant_context.py",
    "nexus_backend/tests/test_ak03_assistant_facts.py",
    "nexus_backend/tests/test_ak04_assistant_execution.py",
    "nexus_backend/tests/test_intelligence_workflow.py::test_positive_ocr_proposal_apply_independent_review_and_export_is_exact_and_idempotent",
]


def validate_junit(path):
    root = ET.parse(path).getroot()
    tests = root.findall(".//testcase")
    if (
        not tests
        or root.findall(".//failure")
        or root.findall(".//error")
        or root.findall(".//skipped")
    ):
        raise BaselineError("tests failed, missing, or skipped")
    for sid in ("normal", "missing", "conflict", "revoked", "quota", "unknown"):
        name = f"test_monthly_delivery_task_real_worker[{sid}]"
        if sum(test.attrib.get("name") == name for test in tests) != 1:
            raise BaselineError("required task missing or repeated")
    for name in (
        "test_fixed_steps_and_handoff_cover_every_blocker_once",
        "test_worker_uses_three_readers_but_sends_once_and_preserves_review",
        "test_customer_has_no_internal_read_steps",
        "test_budget_failure_before_enqueue_has_no_fee_or_send",
        "test_budget_keeps_recent_question_not_oldest_and_preserves_all_facts",
        "test_large_untrusted_history_is_reduced_before_provider_raw_size_guard",
        "test_history_omission_is_explained_by_server_not_model",
        "test_mandatory_request_failure_happens_before_reserve_and_sent_marker",
        *[
            f"test_budget_estimate_matches_actual_gateway_and_output_limit[{mode}]"
            for mode in ("json_schema", "json_object")
        ],
        *[
            f"test_persisted_history_budget_preserves_meter_authorization_and_no_replay[{status}]"
            for status in ("success", "revoked", "unknown", "too_small")
        ],
    ):
        if sum(test.attrib.get("name") == name for test in tests) != 1:
            raise BaselineError("required context budget case missing or repeated")
    return len(tests)


def main(argv=None):
    parser = argparse.ArgumentParser(description="六类助手任务离线基线；无真实模型/数据库/线上访问")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args(argv)
    if not 30 <= args.timeout <= 1800:
        parser.error("timeout 必须为 30–1800 秒")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out = (args.out or ROOT / "tmp_test/assistant-task" / stamp).resolve()
    # Repository source/fixtures must never become a test output directory.
    if out.is_relative_to(ROOT) and not out.is_relative_to(ROOT / "tmp_test"):
        parser.error("仓库内输出仅允许 tmp_test 下的新目录")
    if out.exists():
        parser.error("输出目录已存在，请为本次运行选择新目录")
    out.mkdir(parents=True)
    identity = candidate_identity()
    replay._atomic_json(out / "candidate.json", identity)
    command = [
        sys.executable,
        "-m",
        "pytest",
        *TESTS,
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={out / 'junit.xml'}",
    ]
    env = os.environ.copy()
    # Do not inherit pytest selectors/plugins that could silently skip the gate.
    env.pop("PYTEST_ADDOPTS", None)
    env.pop("PYTEST_PLUGINS", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["ASSISTANT_TASK_REPORT_DIR"] = str(out)
    env["PYTHONIOENCODING"] = "utf-8"
    summary = {
        "candidate_fingerprint": identity["fingerprint"],
        "status": "failed",
        "command": command,
    }
    try:
        with (out / "pytest.log").open("w", encoding="utf-8") as log:
            result = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
                check=False,
            )
        if result.returncode:
            raise BaselineError("pytest failed; inspect local pytest.log")
        count = validate_junit(out / "junit.xml")
        if candidate_identity() != identity:
            raise BaselineError("candidate changed during run")
        report = replay.read_json(out / "report.json", limit=16 * 1024 * 1024)
        if report["engineering"] != "passed" or report["candidate"] != identity:
            raise BaselineError("report candidate/status mismatch")
        summary.update(
            status="passed",
            tests_passed=count,
            real_model_evaluated=False,
            answer_complete_count=report["answer_complete_count"],
            answer_eligible_count=report["answer_eligible_count"],
        )
        summary["artifacts"] = {
            name: replay.sha256_path(out / name)
            for name in (
                "candidate.json",
                "junit.xml",
                "pytest.log",
                "observations.json",
                "report.json",
                "report.md",
            )
        }
    except (BaselineError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        summary["reason"] = f"{type(error).__name__}: {error}"
    replay._atomic_json(out / "summary.json", summary)
    print(f"{summary['status']}: {out / 'summary.json'}")
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
