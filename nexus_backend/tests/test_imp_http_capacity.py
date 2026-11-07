from scripts.imp_http_capacity import validate_rows
import pytest


def test_http_capacity_requires_every_operation_once():
    rows = [{"id": str(i), "ok": True, "status": 200} for i in range(3)]
    validate_rows(rows, 3)
    for bad in (rows[:2], [rows[0]] * 3, rows[:-1] + [{"id": "2", "ok": False, "status": 503}]):
        with pytest.raises(ValueError):
            validate_rows(bad, 3)


def test_tail_latency_is_measured_in_service_capacity_too():
    from tools.capacity.qa_mixed import distribution

    assert distribution(range(1, 101))["p99_ms"] == 99


@pytest.mark.parametrize("major", [16, 17])
def test_capacity_passes_explicit_major_to_identity_fixture(tmp_path, monkeypatch, major):
    import scripts.imp_http_capacity as capacity
    import p02_session_fixture

    config = tmp_path / "regression.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(capacity, "validate_config", lambda value: value)
    monkeypatch.setattr(capacity, "assert_server_and_roles", lambda *args: None)
    monkeypatch.setattr(capacity, "candidate_fingerprint", lambda *args: {})

    class ReachedFixture(Exception):
        pass

    def session(*args, **kwargs):
        assert kwargs.get("major") == major
        raise ReachedFixture

    monkeypatch.setattr(p02_session_fixture, "create_session", session)
    with pytest.raises(ReachedFixture):
        capacity.run(config, major, tmp_path / "http-result")


@pytest.mark.parametrize("major", [16, 17])
def test_identity_fixture_honors_major_without_filename_hint(tmp_path, monkeypatch, major):
    import p02_session_fixture as fixture

    config = tmp_path / "regression.json"
    config.write_text("{}", encoding="utf-8")

    class ServerBoundary(Exception):
        pass

    def validate(value, actual_major):
        assert actual_major == major
        raise ServerBoundary

    monkeypatch.setattr(fixture, "assert_server_and_roles", validate)
    with pytest.raises(ServerBoundary):
        with fixture.create_session(config, tmp_path / "private", major=major):
            pytest.fail("Must verify the actual server before creating any identity")
