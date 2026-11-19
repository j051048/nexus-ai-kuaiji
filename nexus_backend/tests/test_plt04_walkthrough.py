from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "validate_taxi_walkthrough.py"


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def write_sample(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_walkthrough_sample_passes_and_reports_period_amounts(tmp_path: Path):
    path = write_sample(
        tmp_path,
        "taxi-2026-10.csv",
        "交易日期,摘要,金额,交易编号,方向\n"
        "2026-10-01,平台结算,100000.00,T-001,收入\n"
        "2026-10-02,银行手续费,350.00,T-002,支出\n",
    )
    result = run(str(path), "--period", "2026-10", "--json")
    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "passed"
    assert payload["files"][0]["rows"] == 2
    assert payload["files"][0]["total_cents"] == 99_650_00
    assert payload["files"][0]["errors"] == []


def test_walkthrough_rejects_out_of_period_and_bad_amount(tmp_path: Path):
    path = write_sample(
        tmp_path,
        "taxi-bad.csv",
        "交易日期,摘要,金额,交易编号,方向\n"
        "2026-09-30,上月结算,100000.00,T-003,收入\n"
        "2026-10-02,坏金额,12.345,T-004,支出\n",
    )
    result = run(str(path), "--period", "2026-10", "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    errors = "\n".join(payload["files"][0]["errors"])
    assert "不在期间" in errors
    assert "金额无效" in errors


def test_walkthrough_rejects_missing_identity_and_description(tmp_path: Path):
    path = write_sample(
        tmp_path,
        "taxi-shape.csv",
        "交易日期,金额\n2026-10-01,100.00\n",
    )
    result = run(str(path), "--period", "2026-10", "--json")
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    errors = "\n".join(payload["files"][0]["errors"])
    assert "交易编号" in errors
    assert "摘要" in errors
