"""真实签名与模拟官方 HTTP 响应；不依赖或操作任何远端项目。"""

import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.exceptions import PyJWKClientConnectionError

from app.auth import AuthError, AuthSettings, Identity, SupabaseAuthenticator


USER_ID = "bbc278c3-63a4-4a9e-8e88-7094c5b3177c"
OTHER_ID = "a7d9586c-1c30-4436-b2ce-d24ae364c571"
URL = "https://accounting-test.supabase.co"
KEY = "sb_publishable_test_public_key"


@pytest.fixture(scope="module")
def keys():
    return {"RS256": rsa.generate_private_key(public_exponent=65537, key_size=2048),
            "ES256": ec.generate_private_key(ec.SECP256R1())}


def claims(**changes):
    now = int(time.time())
    return {"iss": f"{URL}/auth/v1", "aud": "authenticated", "sub": USER_ID,
            "iat": now - 10, "exp": now + 600, "role": "authenticated", **changes}


def sign(keys, algorithm="RS256", payload=None, headers=None):
    key = keys[algorithm] if algorithm in keys else "test-only-legacy-secret-at-least-32-bytes"
    return jwt.encode(payload if payload is not None else claims(), key, algorithm=algorithm,
                      headers={"kid": f"test-{algorithm}", **(headers or {})})


def jwk(keys, algorithm="RS256", **changes):
    data = jwt.algorithms.get_default_algorithms()[algorithm].to_jwk(keys[algorithm].public_key(), as_dict=True)
    return {**data, "alg": algorithm, "kid": f"test-{algorithm}", "use": "sig", **changes}


def auth(keys, *, user=None, user_status=200, jwks_status=200, jwks_body=None, user_body=None, transport_handler=None, jwks_client=None):
    requests = []

    def handle(request):
        requests.append(request)
        if transport_handler:
            return transport_handler(request)
        if str(request.url) == f"{URL}/auth/v1/.well-known/jwks.json":
            assert "Authorization" not in request.headers
            return httpx.Response(jwks_status, json=jwks_body if jwks_body is not None else {"keys": [jwk(keys, alg) for alg in keys]})
        assert str(request.url) == f"{URL}/auth/v1/user"
        assert request.headers["apikey"] == KEY
        assert request.headers["Authorization"].startswith("Bearer ")
        if user_body is not None:
            return httpx.Response(user_status, content=user_body)
        return httpx.Response(user_status, json=user if user is not None else {
            "id": USER_ID, "email": "reviewer@example.test", "email_confirmed_at": "2020-01-01T00:00:00Z", "user_metadata": {"full_name": "验证会计"},
        })

    client = httpx.Client(transport=httpx.MockTransport(handle))
    return SupabaseAuthenticator(AuthSettings("supabase", URL, KEY), http_client=client, jwks_client=jwks_client), requests


def assert_denied(authenticator, token, status=401):
    with pytest.raises(AuthError) as error:
        authenticator.authenticate(token)
    assert error.value.status == status
    assert token not in error.value.detail if isinstance(token, str) and token else True


@pytest.mark.parametrize("algorithm", ["RS256", "ES256", "HS256"])
def test_valid_user_identity_comes_from_online_user_and_never_contains_permissions(keys, algorithm):
    authenticator, requests = auth(keys, user={
        "id": USER_ID, "email": "reviewer@example.test", "email_confirmed_at": "2020-01-01T00:00:00Z", "user_metadata": {"full_name": "验证会计", "role": "owner"},
        "app_metadata": {"firm_id": "attacker-firm", "role": "owner"},
    })
    token = sign(keys, algorithm=algorithm, payload=claims(user_metadata={"full_name": "伪造姓名"}, app_metadata={"role": "owner"}))
    assert authenticator.authenticate(token) == Identity(USER_ID, "reviewer@example.test", "验证会计")
    assert requests[-1].headers["Authorization"] == f"Bearer {token}"
    assert len(requests) == (1 if algorithm == "HS256" else 2)


def test_small_provider_clock_skew_on_issued_at_is_accepted(keys):
    authenticator, _ = auth(keys)
    now = int(time.time())
    token = sign(keys, payload=claims(iat=now + 5, exp=now + 600))
    assert authenticator.authenticate(token).user_id == USER_ID


def test_jwks_is_cached_but_online_user_is_verified_for_each_request(keys):
    authenticator, requests = auth(keys)
    token = sign(keys)
    authenticator.authenticate(token)
    authenticator.authenticate(token)
    assert [request.url.path for request in requests] == ["/auth/v1/.well-known/jwks.json", "/auth/v1/user", "/auth/v1/user"]


@pytest.mark.parametrize("algorithm", ["RS256", "ES256", "HS256"])
def test_admin_assurance_comes_only_from_verified_token_claims(keys, algorithm):
    authenticator, _ = auth(keys)
    verified = sign(keys, algorithm=algorithm, payload=claims(aal="aal2"))
    assert authenticator.authenticate(verified).aal == "aal2"
    untrusted = sign(keys, algorithm=algorithm, payload=claims(user_metadata={"aal": "aal2"}))
    assert authenticator.authenticate(untrusted).aal == "aal1"
    unknown = sign(keys, algorithm=algorithm, payload=claims(aal="owner"))
    assert authenticator.authenticate(unknown).aal == "aal1"


@pytest.mark.parametrize("algorithm", ["RS256", "HS256"])
@pytest.mark.parametrize("change", [
    {"iss": "https://attacker.supabase.co/auth/v1"},
    {"iss": f"{URL}/auth/v1/"},
    {"aud": "service_role"},
    {"exp": 1},
    {"iat": int(time.time()) + 3600},
    {"nbf": int(time.time()) + 3600},
    {"exp": "9999999999"},
    {"exp": True},
    {"iat": "1"},
    {"iat": -1, "exp": -2},
    {"sub": "not-a-user-uuid"},
    {"sub": OTHER_ID},
])
def test_rejects_claim_changes_even_when_online_legacy_verification_succeeds(keys, algorithm, change):
    authenticator, _ = auth(keys)
    assert_denied(authenticator, sign(keys, algorithm=algorithm, payload=claims(**change)))


@pytest.mark.parametrize("algorithm", ["RS256", "HS256"])
@pytest.mark.parametrize("missing", ["iss", "aud", "exp", "sub", "iat"])
def test_requires_all_standard_access_token_claims(keys, algorithm, missing):
    payload = claims()
    del payload[missing]
    authenticator, _ = auth(keys)
    assert_denied(authenticator, sign(keys, algorithm=algorithm, payload=payload))


@pytest.mark.parametrize("token", [None, "", "junk", "a.b.c", "x" * 16385, " token "])
def test_rejects_malformed_bearer_before_network(keys, token):
    authenticator, requests = auth(keys)
    assert_denied(authenticator, token)
    assert requests == []


@pytest.mark.parametrize("algorithm", ["none", "HS384", "RS384"])
def test_fixed_algorithm_allowlist_rejects_other_algorithms(keys, algorithm):
    token_key = None if algorithm == "none" else keys["RS256"] if algorithm == "RS384" else "test-only-legacy-secret-for-sha384-at-least-forty-eight-bytes"
    token = jwt.encode(claims(), token_key, algorithm=algorithm)
    authenticator, requests = auth(keys)
    assert_denied(authenticator, token)
    assert requests == []


def test_tampered_signature_never_reaches_online_identity(keys):
    token = sign(keys)
    head, payload, signature = token.split(".")
    authenticator, requests = auth(keys)
    assert_denied(authenticator, f"{head}.{payload}.{signature[:8]}AAAA{signature[12:]}")
    assert all(request.url.path.endswith("jwks.json") for request in requests)


@pytest.mark.parametrize("kid", ["", None, 1, "x" * 257])
def test_asymmetric_token_requires_valid_key_identifier(keys, kid):
    token = sign(keys, headers={"kid": str(kid)})
    header = jwt.get_unverified_header(token)
    header["kid"] = kid
    # 手工只修改 header，用于测试格式拒绝，签名无需有效。
    header_part = jwt.utils.base64url_encode(json.dumps(header).encode()).decode()
    token = ".".join([header_part, *token.split(".")[1:]])
    authenticator, requests = auth(keys)
    assert_denied(authenticator, token)
    assert requests == []


def test_unknown_kid_refreshes_keys_and_denies_token(keys):
    authenticator, requests = auth(keys)
    authenticator.authenticate(sign(keys))
    assert_denied(authenticator, sign(keys, headers={"kid": "not-in-project"}))
    assert len([request for request in requests if request.url.path.endswith("jwks.json")]) == 2


def test_key_rotation_refreshes_unknown_kid_without_restarting(keys):
    new_key = ec.generate_private_key(ec.SECP256R1())
    rotated_jwk = jwt.algorithms.ECAlgorithm.to_jwk(new_key.public_key(), as_dict=True)
    rotated_jwk.update({"kid": "rotated-key", "alg": "ES256"})
    count = 0

    def handle(request):
        nonlocal count
        if request.url.path.endswith("jwks.json"):
            count += 1
            return httpx.Response(200, json={"keys": [jwk(keys)] + ([rotated_jwk] if count > 1 else [])})
        return httpx.Response(200, json={"id": USER_ID, "email": "reviewer@example.test", "email_confirmed_at": "2020-01-01T00:00:00Z"})

    authenticator, _ = auth(keys, transport_handler=handle)
    authenticator.authenticate(sign(keys))
    rotated_token = jwt.encode(claims(), new_key, algorithm="ES256", headers={"kid": "rotated-key"})
    assert authenticator.authenticate(rotated_token).user_id == USER_ID
    assert count == 2


def test_key_algorithm_confusion_is_rejected(keys):
    # 即使 kid 对应公钥存在，也不能以另一算法使用该密钥。
    authenticator, _ = auth(keys, jwks_body={"keys": [jwk(keys, "ES256", kid="test-RS256")]})
    assert_denied(authenticator, sign(keys))


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_auth_service_rejecting_user_is_401(keys, status):
    authenticator, _ = auth(keys, user_status=status)
    assert_denied(authenticator, sign(keys), 401)


@pytest.mark.parametrize("status", [301, 404, 429, 500, 503])
def test_auth_service_failure_is_503_and_does_not_return_identity(keys, status):
    authenticator, _ = auth(keys, user_status=status)
    assert_denied(authenticator, sign(keys), 503)


@pytest.mark.parametrize("status", [301, 404, 429, 503])
def test_jwks_failure_is_503(keys, status):
    authenticator, _ = auth(keys, jwks_status=status)
    assert_denied(authenticator, sign(keys), 503)


@pytest.mark.parametrize("payload", [[], {"keys": "wrong"}, {"keys": [{"kty": "RSA", "alg": "RS256", "kid": "broken"}]}])
def test_malformed_jwks_fails_closed(keys, payload):
    authenticator, _ = auth(keys, jwks_body=payload)
    assert_denied(authenticator, sign(keys), 503)


def test_http_timeout_is_503(keys):
    def handle(request):
        raise httpx.ReadTimeout("mock timeout", request=request)

    authenticator, _ = auth(keys, transport_handler=handle)
    assert_denied(authenticator, sign(keys), 503)


def test_injected_pyjwt_jwks_connection_failure_is_503(keys):
    class UnreachableJwks:
        def get_signing_key_from_jwt(self, token):
            raise PyJWKClientConnectionError("mock unavailable")

    authenticator, _ = auth(keys, jwks_client=UnreachableJwks())
    assert_denied(authenticator, sign(keys), 503)


@pytest.mark.parametrize("user", [
    {"id": OTHER_ID},
    {"id": USER_ID, "is_anonymous": True},
    {"id": USER_ID, "deleted_at": "2026-01-01T00:00:00Z"},
    {"id": USER_ID, "banned_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()},
])
def test_disabled_deleted_anonymous_or_subject_mismatch_user_is_rejected(keys, user):
    authenticator, _ = auth(keys, user=user)
    assert_denied(authenticator, sign(keys))


@pytest.mark.parametrize("user", [{}, {"id": "broken"}, {"id": USER_ID, "email": {"bad": "shape"}}, {"id": USER_ID, "banned_until": "not-a-time"}])
def test_malformed_online_user_response_is_503(keys, user):
    authenticator, _ = auth(keys, user=user)
    assert_denied(authenticator, sign(keys), 503)


def test_malformed_online_json_response_is_503(keys):
    authenticator, _ = auth(keys, user_body="not-json")
    assert_denied(authenticator, sign(keys), 503)


def test_expired_ban_and_missing_display_name_do_not_block_valid_identity(keys):
    authenticator, _ = auth(keys, user={"id": USER_ID, "banned_until": "2020-01-01T00:00:00Z", "email": "reviewer@example.test", "email_confirmed_at": "2020-01-01T00:00:00Z"})
    assert authenticator.authenticate(sign(keys)).display_name == "reviewer@example.test"


@pytest.mark.parametrize("confirmation", [None, "", False])
def test_unconfirmed_email_cannot_become_assignment_identity(keys, confirmation):
    authenticator, _ = auth(keys, user={"id": USER_ID, "email": "reviewer@example.test", "email_confirmed_at": confirmation})
    assert_denied(authenticator, sign(keys))


def test_small_provider_clock_skew_on_confirmation_is_accepted(keys):
    future = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat().replace("+00:00", "Z")
    authenticator, _ = auth(keys, user={"id": USER_ID, "email": "reviewer@example.test", "email_confirmed_at": future})
    assert authenticator.authenticate(sign(keys)).user_id == USER_ID


@pytest.mark.parametrize("confirmation", ["not-a-date", "2020-01-01", "2999-01-01T00:00:00Z"])
def test_invalid_provider_confirmation_fails_closed(keys, confirmation):
    authenticator, _ = auth(keys, user={"id": USER_ID, "email": "reviewer@example.test", "email_confirmed_at": confirmation})
    assert_denied(authenticator, sign(keys), status=503)


def test_auth_settings_are_local_by_default_and_public_config_does_not_leak_other_environment():
    assert AuthSettings.from_env({}).public_config() == {"mode": "local"}
    settings = AuthSettings.from_env({"ACCOUNTING_AUTH_MODE": "supabase", "ACCOUNTING_SUPABASE_URL": URL + "/", "ACCOUNTING_SUPABASE_PUBLISHABLE_KEY": KEY, "ACCOUNTING_DATABASE_URL": "secret-db-url"})
    assert settings.public_config() == {"mode": "supabase", "supabase_url": URL, "publishable_key": KEY}
    assert KEY not in repr(settings)


@pytest.mark.parametrize("env", [
    {"ACCOUNTING_AUTH_MODE": "bad"},
    {"ACCOUNTING_AUTH_MODE": "supabase"},
    {"ACCOUNTING_AUTH_MODE": "supabase", "ACCOUNTING_SUPABASE_URL": URL},
])
def test_invalid_auth_mode_or_missing_config_refuses_startup(env):
    with pytest.raises(RuntimeError):
        AuthSettings.from_env(env)


@pytest.mark.parametrize("url", ["http://accounting-test.supabase.co", "https://127.0.0.1", "https://localhost", "https://localhost.test:8080", "https://u:p@accounting-test.supabase.co", URL + "/auth/v1", URL + "?target=other", URL + "#x", "https://accounting-test.supabase.co:broken"])
def test_supabase_configuration_requires_fixed_https_project_origin(url):
    with pytest.raises(RuntimeError):
        AuthSettings("supabase", url, KEY)


@pytest.mark.parametrize("key", ["sb_secret_never_public", "service-role-key", jwt.encode({"role": "service_role"}, "test-key-at-least-thirty-two-bytes", algorithm="HS256"), jwt.encode({"role": "authenticated"}, "test-key-at-least-thirty-two-bytes", algorithm="HS256")])
def test_secret_and_service_role_keys_cannot_be_public_configuration(key):
    with pytest.raises(RuntimeError) as error:
        AuthSettings("supabase", URL, key)
    assert key not in str(error.value)


def test_legacy_anon_api_key_is_accepted_only_as_configuration():
    anon = jwt.encode({"role": "anon"}, "test-key-at-least-thirty-two-bytes", algorithm="HS256")
    settings = AuthSettings("supabase", URL, anon)
    assert settings.public_config()["publishable_key"] == anon
