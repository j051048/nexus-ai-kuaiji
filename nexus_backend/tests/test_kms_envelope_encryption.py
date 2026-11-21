"""信封加密：往返、篡改识别、密钥轮换与配置错误。

密钥轮换是这里唯一值得单独测的东西：它决定了「换密钥」是一次性上线动作，
还是必须停机把所有数据搬一遍。
"""

import base64
import os

import pytest

from app.security.kms import (
    ACTIVE_ENV,
    KEYS_ENV,
    Envelope,
    KmsError,
    LocalEnvKeyProvider,
)


def _raw(byte: int) -> bytes:
    return bytes([byte]) * 32


def _key(byte: int) -> str:
    """base64 form, as it appears in the environment variable."""
    return base64.b64encode(_raw(byte)).decode("ascii")


def _provider(*, active=None, extra=None):
    keys = {"k1": _raw(1), "k2": _raw(2)}
    if extra:
        keys.update(extra)
    return LocalEnvKeyProvider(keys, active or "k1")


def test_roundtrip_carries_the_key_id_and_survives_json():
    provider = _provider()

    envelope = provider.encrypt("银行回单原件".encode())

    assert envelope.key_id == "k1"
    restored = Envelope.from_json(envelope.to_json())
    assert provider.decrypt(restored) == "银行回单原件".encode()


def test_ciphertext_is_not_plaintext_and_uses_a_fresh_nonce():
    provider = _provider()

    first = provider.encrypt(b"same payload")
    second = provider.encrypt(b"same payload")

    assert first.nonce != second.nonce
    assert b"same payload" not in first.ciphertext


def test_tampered_ciphertext_is_refused_instead_of_returning_garbage():
    provider = _provider()
    envelope = provider.encrypt(b"amount=100")
    flipped = bytearray(envelope.ciphertext)
    flipped[0] ^= 0x01

    with pytest.raises(KmsError) as caught:
        provider.decrypt(Envelope(envelope.key_id, envelope.nonce, bytes(flipped)))

    assert "篡改" in str(caught.value)


def test_associated_data_must_match():
    provider = _provider()
    envelope = provider.encrypt(b"attached to firm-a", aad=b"firm-a")

    assert provider.decrypt(envelope, aad=b"firm-a") == b"attached to firm-a"
    with pytest.raises(KmsError):
        provider.decrypt(envelope, aad=b"firm-b")


def test_rotation_keeps_old_records_readable_and_moves_them_forward():
    old = _provider()
    legacy = old.encrypt(b"archived 2025 ledger")

    rotated = _provider(active="k2")
    assert rotated.decrypt(legacy) == b"archived 2025 ledger"

    moved = rotated.reencrypt(legacy)
    assert moved.key_id == "k2"
    assert rotated.decrypt(moved) == b"archived 2025 ledger"


def test_dropping_the_old_key_fails_loudly_instead_of_guessing():
    legacy = _provider().encrypt(b"x")
    keyless = LocalEnvKeyProvider({"k2": _raw(2)}, "k2")

    with pytest.raises(KmsError) as caught:
        keyless.decrypt(legacy)

    assert "k1" in str(caught.value)


def test_environment_ring_parsing_and_active_key_default():
    environ = {KEYS_ENV: f"k1:{_key(1)}, k2:{_key(2)}"}

    provider = LocalEnvKeyProvider.from_env(environ)

    assert provider.active_key_id == "k1"
    assert provider.decrypt(provider.encrypt(b"ok")) == b"ok"

    explicit = LocalEnvKeyProvider.from_env({**environ, ACTIVE_ENV: "k2"})
    assert explicit.active_key_id == "k2"


@pytest.mark.parametrize(
    "environ,message",
    [
        ({}, "没有可用的信封密钥"),
        ({KEYS_ENV: "k1:not-base64!"}, "不是合法 base64"),
        ({KEYS_ENV: "nocolon"}, "格式应为"),
        ({KEYS_ENV: f"k1:{_key(1)}", ACTIVE_ENV: "k9"}, "不在"),
        ({KEYS_ENV: f"k1:{base64.b64encode(b'short').decode()}"}, "长度必须是"),
        ({KEYS_ENV: f"k1:{_key(1)},k1:{_key(2)}"}, "重复"),
    ],
)
def test_bad_configuration_refuses_to_start(environ, message):
    with pytest.raises(KmsError) as caught:
        LocalEnvKeyProvider.from_env(environ)

    assert message in str(caught.value)


def test_envelope_parsing_rejects_unknown_versions_and_junk():
    with pytest.raises(KmsError):
        Envelope.from_json(b"not json")
    with pytest.raises(KmsError):
        Envelope.from_json('{"v":2,"alg":"A256GCM","key_id":"k1","nonce":"AAAA","ct":"AAAA"}')
    with pytest.raises(KmsError):
        Envelope.from_json('{"v":1,"alg":"A256GCM","key_id":"","nonce":"AAAA","ct":"AAAA"}')
    assert len(os.urandom(12)) == 12
