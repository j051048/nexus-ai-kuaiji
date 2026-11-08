"""The doctor may verify dependencies, never imply PG or release acceptance."""

import json
from scripts import env_doctor


def test_doctor_machine_report_is_scoped_and_secret_free(monkeypatch):
    monkeypatch.setattr(
        env_doctor.metadata, "version", lambda _: (_ for _ in ()).throw(RuntimeError("SECRET-DSN"))
    )
    report = env_doctor.inspect_environment()
    assert report["status"] == "blocked"
    assert report["scope"] == "local_dependencies_only"
    assert {"postgres16", "postgres17", "joint_recovery", "real_channels"} <= set(
        report["not_checked"]
    )
    assert "SECRET-DSN" not in json.dumps(report)
    assert any(c["code"] == "dependency_probe_failed" for c in report["checks"])


def test_layers_keep_required_pg_recovery_and_browser_jobs():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    layers = json.loads((root / "scripts/test_layers.json").read_text(encoding="utf-8"))
    assert layers["quick"]["release_eligible"] is False
    assert {"accounting.yml", "p01-recovery.yml", "qa-reliability.yml"} <= set(
        layers["release"]["workflows"]
    )
    for name in layers["release"]["workflows"]:
        data = (root / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "push" in data and "pull_request" in data and "schedule:" in data
    assert "local-quick.yml" in layers["quick"]["workflows"]
