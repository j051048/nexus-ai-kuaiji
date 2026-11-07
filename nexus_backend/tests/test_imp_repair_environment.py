import json
from pathlib import Path

import pytest

from scripts import p01_local_recovery_env as env
import c01_wsl_bridge as client
from scripts import c01_local_wsl_bridge as server


def test_recovery_can_reserve_an_independent_bridge_port(tmp_path):
    plan = env.make_plan(tmp_path, tmp_path, tmp_path, bridge_port=25540)
    assert plan["bridge_port"] == 25540
    for port in (True, 5432, 25496, 25511, 65536):
        with pytest.raises(env.RecoveryEnvironmentError):
            env.make_plan(tmp_path, tmp_path, tmp_path, bridge_port=port)


def test_bridge_client_uses_frozen_port_not_another_run(tmp_path, monkeypatch):
    config = tmp_path / "bridge.json"
    config.write_text(json.dumps({"token": "synthetic", "bridge_port": 25540}))
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return b"{}"

    class Opener:
        def open(self, request, **kwargs):
            seen.append(request.full_url)
            return Response()

    monkeypatch.setattr(client, "build_opener", lambda *_: Opener())
    client.call(config, "status")
    assert seen == ["http://127.0.0.1:25540/v1/jobs"]


def test_server_accepts_cli_string_path_and_same_port(tmp_path, monkeypatch):
    config = tmp_path / "bridge.json"
    config.write_text(json.dumps({"token": "synthetic", "bridge_port": 25540}))
    seen = []

    class Bridge:
        def __init__(self, _):
            pass

        def stop_all(self):
            pass

    class Server:
        def __init__(self, address, _):
            seen.append(address)

        def serve_forever(self):
            pass

        def server_close(self):
            pass

    monkeypatch.setattr(server, "Bridge", Bridge)
    monkeypatch.setattr(server, "ThreadingHTTPServer", Server)
    server.serve(str(config))
    assert seen == [("127.0.0.1", 25540)]


def test_auth_ports_are_frozen_distinct_and_local():
    assert env.auth_ports({"auth_ports": [25541, 25542], "bridge_port": 25540}) == [25541, 25542]
    for ports in ([25541, 25541], [True, 25542], [5432, 25542], [25496, 25542], [25540, 25542]):
        with pytest.raises(env.RecoveryEnvironmentError):
            env.auth_ports({"auth_ports": ports, "bridge_port": 25540})


def test_empty_optional_proxy_keeps_loopback(monkeypatch):
    from c01_gotrue_fixture import _LocalAuthTransport

    monkeypatch.setenv("C01_AUTH_PROXY_HOST", "")
    transport = _LocalAuthTransport("https://synthetic.example.test", 25541)
    try:
        assert transport.reach_host == "127.0.0.1"
        assert transport.reach_port == 25541
    finally:
        transport.close()


def test_browser_preflight_uses_frozen_auth_port(tmp_path):
    from scripts.p02_browser_gate import required_ports

    (tmp_path / "wsl-bridge-private.json").write_text(
        json.dumps({"bridge_port": 25540, "auth_ports": [25541, 25542]})
    )
    assert required_ports(tmp_path) == (18100, 18180, 25541)
