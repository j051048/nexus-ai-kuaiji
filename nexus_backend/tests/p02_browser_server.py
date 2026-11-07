"""Explicit localhost-only P02 acceptance host; no production configuration."""

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "nexus_backend"), str(ROOT / "nexus_backend/tests"), str(ROOT)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    output = Path(args.out).resolve()
    if not output.is_relative_to(ROOT / "tmp_test") or output.exists():
        raise ValueError("Require a fresh local tmp_test directory")
    output.mkdir(parents=True)
    for name in list(os.environ):
        if name.upper().startswith(
            ("ACCOUNTING_", "SUPABASE_", "OPENAI_", "DEEPSEEK_", "ZEABUR_", "VERCEL_", "PG")
        ) or name.upper() in {"DATABASE_URL", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}:
            os.environ.pop(name, None)
    os.environ.update(ACCOUNTING_ENV="test", ACCOUNTING_ALLOWED_ORIGINS="http://127.0.0.1:18180")
    real_connect, real_dns = socket.socket.connect, socket.getaddrinfo

    def local_connect(instance, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "localhost", "::1"}:
            raise RuntimeError("P02 browser host forbids external network")
        return real_connect(instance, address)

    def local_dns(host, *values, **kwargs):
        if host not in {None, "127.0.0.1", "localhost", "::1", b"127.0.0.1"}:
            raise RuntimeError("P02 browser host forbids external DNS")
        return real_dns(host, *values, **kwargs)

    socket.socket.connect, socket.getaddrinfo = local_connect, local_dns
    import uvicorn
    from c01_gotrue_fixture import RealGoTrue, create_auth_database
    from c01_identity_fixture import _secure, _private_write
    from p02_session_fixture import create_session
    from p02_business_fixture import seed_nonzero_flow, complete_close_delivery, call

    _secure(output)
    with ExitStack() as stack:
        identity = stack.enter_context(
            RealGoTrue(create_auth_database(17, target=False), output / "auth", 25511)
        )
        cases = {}
        sessions = []
        for mode in ("desktop", "mobile"):
            session = stack.enter_context(
                create_session(Path(args.config), output / "application", identity=identity)
            )
            sessions.append(session)
            state = seed_nonzero_flow(
                session.client, session.headers, session.ids, stop_before_coverage_review=True
            )
            complete_close_delivery(
                session.client,
                session.headers,
                session.ids,
                state,
                stop_after_report_validation=True,
            )
            call(
                session.client,
                "PUT",
                f"/api/books/{state['book_id']}/service-plan",
                session.headers["owner"],
                {
                    "expected_version": 0,
                    "contact_user_id": session.ids["users"]["customer"],
                    "due_on": "2026-11-10",
                    "reason": "P02本机浏览器精确版本签收验证",
                },
            )
            call(
                session.client,
                "POST",
                f"/api/books/{state['book_id']}/delivery/monthly-followup/recipient",
                session.headers["reviewer"],
                {
                    "period": state["period"],
                    "recipient_user_id": session.ids["users"]["customer"],
                    "reason": "普通复核员仅配置当前客户收件人",
                },
                201,
            )
            accounts = {
                dest: session.credentials[src]
                for dest, src in {
                    "maker": "accountant",
                    "reviewer": "reviewer",
                    "client": "customer",
                    "foreign": "outsider",
                }.items()
            }
            cases[mode] = {**state, "accounts": accounts}
            print(f"P02 {mode} nonzero bank/subledger prepared", flush=True)
        config = uvicorn.Config(
            sessions[0].app,
            host="127.0.0.1",
            port=18100,
            lifespan="off",
            access_log=False,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 15
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("P02 local API did not start")
            time.sleep(0.05)
        _private_write(
            output / "fixture-private.json",
            json.dumps(
                {
                    "auth_origin": identity.origin,
                    "auth_local_origin": f"http://127.0.0.1:{identity.port}",
                    "cases": cases,
                },
                ensure_ascii=False,
            ).encode(),
        )
        (output / "ready.json").write_text(
            json.dumps({"api": "http://127.0.0.1:18100", "cases": list(cases)}), encoding="utf-8"
        )
        print("P02 browser API ready; create stop file for orderly shutdown", flush=True)
        try:
            while thread.is_alive() and not (output / "stop").exists():
                time.sleep(0.25)
        finally:
            server.should_exit = True
            thread.join(15)
            if thread.is_alive():
                raise RuntimeError("P02 API failed to drain; keep database resources alive")


if __name__ == "__main__":
    main()
