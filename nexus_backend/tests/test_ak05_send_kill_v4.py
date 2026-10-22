"""AK05: kill a real child process after OCR provider entry, then recover."""
import os
import subprocess
import sys
import time
from pathlib import Path

from test_intelligence_workflow import ai_case, create_event
from test_queue_workflow import enqueue, queue_case


def _run(case, tmp_path, mode, worker_id):
    script = Path(__file__).with_name("ak05_child.py")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(Path(__file__).resolve().parents[2] / "nexus_backend"),
            str(Path(__file__).resolve().parent),
            str(Path(__file__).resolve().parents[2]),
        ]
    )
    return subprocess.Popen(
        [
            sys.executable,
            str(script),
            "--db",
            case.db.path,
            "--storage",
            str(case.storage.root),
            "--worker-id",
            worker_id,
            "--mode",
            mode,
            "--marker",
            str(tmp_path / "marker"),
            "--counter",
            str(tmp_path / "counter"),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_send_hook_kill_then_recovery_does_not_resend(queue_case, tmp_path):
    case = queue_case
    enqueue(case, [create_event(case)])
    child = _run(case, tmp_path, "hold", "ak05-hold")
    deadline = time.monotonic() + 10
    while not (tmp_path / "marker").exists() and time.monotonic() < deadline:
        if child.poll() is not None:
            break
        time.sleep(0.02)
    assert (tmp_path / "marker").exists(), (child.stdout.read(), child.stderr.read())
    child.kill()
    child.wait(5)

    recovered = _run(case, tmp_path, "recover", "ak05-recover")
    out, err = recovered.communicate(20)
    assert recovered.returncode == 0, (out, err)
    assert not (tmp_path / "counter").exists(), "恢复进程不得重复发送"
    with case.db.transaction() as conn:
        row = conn.execute("SELECT phase,status FROM ocr_attempts").fetchone()
    assert row["phase"] == "sending" and row["status"] == "running"
