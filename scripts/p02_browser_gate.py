"""Real isolated PG/GoTrue browser gate, with owned process cleanup and public proof."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.release_candidate import candidate_fingerprint
from scripts.p01_local_recovery_env import port_is_occupied, auth_ports


def required_ports(config_dir):
    config = json.loads(
        (Path(config_dir) / "wsl-bridge-private.json").read_text(encoding="utf-8-sig")
    )
    return (18100, 18180, auth_ports(config)[0])


def validate_result(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    names = [case.get("name", "") for case in cases]
    expected = {
        "P02 initial 403 clears loading and permits explicit reread",
        "P02 revoked late write cannot restore data or success",
        "P02 foreign company book and reports stay invisible in the browser",
        "P02 client identity cannot review, lock or batch export",
        *[
            f"P02 real PG signed identity {flow} {view}"
            for flow in ("month-close", "delivery and acknowledgement")
            for view in ("desktop", "mobile")
        ],
    }
    if (
        len(cases) != 8
        or set(names) != expected
        or any(
            case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")
        )
    ):
        raise ValueError("Browser case inventory incomplete or not all passing")
    return {"passed": len(cases), "skipped": 0, "cases": sorted(names)}


def run(config_dir, output, node="node"):
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT / "tmp_test") or output.exists():
        raise ValueError("Use a new local tmp_test output directory")
    config_dir = Path(config_dir).resolve()
    if not config_dir.is_relative_to(ROOT / "tmp_test"):
        raise ValueError("Explicit isolated run configuration required")
    # Never attach to or terminate a developer's server.
    for port in required_ports(config_dir):
        if port_is_occupied(port):
            raise ValueError("Local browser acceptance port already has a listener")
    output.mkdir(parents=True)
    before = candidate_fingerprint(ROOT)
    report = {"status": "failed", "candidate": before}
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.upper().startswith(
            (
                "ACCOUNTING_",
                "VITE_",
                "SUPABASE_",
                "OPENAI_",
                "DEEPSEEK_",
                "SMTP_",
                "DELIVERY_",
                "PG",
                "DATABASE_",
            )
        )
        and k.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
    }
    host_dir = output / "host-private"
    env.update(
        P01_CONFIG_DIR=str(config_dir),
        E2E_LOCAL_ISOLATION="1",
        ACCOUNTING_API_PROXY="http://127.0.0.1:18100",
        P02_BROWSER_FIXTURE=str(host_dir / "fixture-private.json"),
        P02_EXPECTED_AUTH_LOCAL_ORIGIN=f"http://127.0.0.1:{required_ports(config_dir)[2]}",
        PLAYWRIGHT_JUNIT_OUTPUT_FILE=str(output / "browser-private.xml"),
        P02_BROWSER_OUTPUT=str(output / "browser-private"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    host = frontend = None
    with ExitStack() as stack:

        def start(command, name):
            log = stack.enter_context((output / name).open("xb"))
            return subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )

        try:
            host = start(
                [
                    sys.executable,
                    "-B",
                    "nexus_backend/tests/p02_browser_server.py",
                    "--config",
                    str(config_dir / "pg17-recovery_source.json"),
                    "--out",
                    str(host_dir),
                ],
                "host-private.log",
            )
            deadline = time.monotonic() + 180
            while not (host_dir / "ready.json").is_file():
                if host.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Local business fixture failed; inspect private host log")
                time.sleep(0.25)
            frontend = start(
                [
                    node,
                    "node_modules/vite/bin/vite.js",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    "18180",
                    "--strictPort",
                ],
                "frontend-private.log",
            )
            deadline = time.monotonic() + 60
            while True:
                try:
                    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                        "http://127.0.0.1:18180", timeout=2
                    ):
                        break
                except OSError:
                    if frontend.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Local frontend failed") from None
                    time.sleep(0.25)
            with (output / "browser-private.log").open("xb") as log:
                result = subprocess.run(
                    [
                        node,
                        "node_modules/@playwright/test/cli.js",
                        "test",
                        "--config=playwright.p02.config.ts",
                        "--reporter=line,junit",
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    timeout=1200,
                )
            if result.returncode:
                raise RuntimeError("Browser validation failed; inspect private browser log")
            report.update(validate_result(output / "browser-private.xml"))
            if candidate_fingerprint(ROOT) != before:
                raise RuntimeError("Candidate changed during browser validation")
            report["status"] = "passed"
        except Exception as error:
            report["error_class"] = type(error).__name__
        finally:
            if frontend is not None and frontend.poll() is None:
                frontend.terminate()
                frontend.wait(timeout=15)
            if host is not None and host.poll() is None:
                (host_dir / "stop").touch()
                try:
                    host.wait(timeout=45)
                except subprocess.TimeoutExpired:
                    # Do not close a pool under a live thread; report non-drain.
                    report["status"] = "failed"
                    report["cleanup"] = "host_drain_timeout"
        (output / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps({"status": report["status"], "report": str(output / "summary.json")}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    raise SystemExit(run(args.config_dir, args.out, args.node))
