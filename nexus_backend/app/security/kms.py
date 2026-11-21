"""Application-level envelope encryption with rotation-friendly key ids.

Every ciphertext carries the id of the key that produced it, so rotating keys is
a data operation, not a deployment flag day: the new key encrypts, the old key
stays in the ring to decrypt, and `reencrypt` walks existing rows at leisure.

The provider is an interface on purpose. `LocalEnvKeyProvider` reads keys from
the environment (dev, tests, single-node deploys); a KMS/HSM adapter implements
the same two calls, and nothing above this module needs to know which one runs.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEYS_ENV = "ACCOUNTING_KMS_KEYS"
ACTIVE_ENV = "ACCOUNTING_KMS_ACTIVE_KEY_ID"
KEY_BYTES = 32
NONCE_BYTES = 12


class KmsError(Exception):
    """Raised for anything the caller must not swallow silently."""


@dataclass(frozen=True)
class Envelope:
    key_id: str
    nonce: bytes
    ciphertext: bytes

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": 1,
                "alg": "A256GCM",
                "key_id": self.key_id,
                "nonce": base64.b64encode(self.nonce).decode("ascii"),
                "ct": base64.b64encode(self.ciphertext).decode("ascii"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> "Envelope":
        try:
            data = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise KmsError("信封不是合法 JSON") from error
        if not isinstance(data, dict) or data.get("v") != 1 or data.get("alg") != "A256GCM":
            raise KmsError("信封版本或算法不受支持")
        key_id = data.get("key_id")
        if not isinstance(key_id, str) or not key_id:
            raise KmsError("信封缺少密钥标识")
        try:
            nonce = base64.b64decode(data["nonce"], validate=True)
            ciphertext = base64.b64decode(data["ct"], validate=True)
        except (KeyError, binascii.Error, ValueError) as error:
            raise KmsError("信封载荷无法解码") from error
        if len(nonce) != NONCE_BYTES or not ciphertext:
            raise KmsError("信封载荷长度不合法")
        return cls(key_id=key_id, nonce=nonce, ciphertext=ciphertext)


class KeyProvider(Protocol):
    @property
    def active_key_id(self) -> str: ...

    def encrypt(self, plaintext: bytes, *, aad: bytes = b"") -> Envelope: ...

    def decrypt(self, envelope: Envelope, *, aad: bytes = b"") -> bytes: ...


class LocalEnvKeyProvider:
    """Key ring from the environment: ``id:base64key`` pairs, comma separated."""

    def __init__(self, keys: dict[str, bytes], active_key_id: str):
        if not keys:
            raise KmsError(f"没有可用的信封密钥：请设置 {KEYS_ENV}（格式 keyId:base64key）")
        for key_id, key in keys.items():
            if len(key) != KEY_BYTES:
                raise KmsError(f"密钥 {key_id} 长度必须是 {KEY_BYTES} 字节")
        if active_key_id not in keys:
            raise KmsError(f"{ACTIVE_ENV}={active_key_id} 不在 {KEYS_ENV} 的密钥环里")
        self._keys = dict(keys)
        self._active = active_key_id

    @classmethod
    def from_env(cls, environ=None) -> "LocalEnvKeyProvider":
        environ = os.environ if environ is None else environ
        raw = environ.get(KEYS_ENV, "").strip()
        if not raw:
            raise KmsError(f"没有可用的信封密钥：请设置 {KEYS_ENV}（格式 keyId:base64key）")
        keys: dict[str, bytes] = {}
        for item in raw.split(","):
            item = item.strip()
            if not item:
                continue
            key_id, _, encoded = item.partition(":")
            key_id = key_id.strip()
            if not key_id or not encoded:
                raise KmsError(f"{KEYS_ENV} 条目格式应为 keyId:base64key")
            if key_id in keys:
                raise KmsError(f"{KEYS_ENV} 里密钥标识重复：{key_id}")
            try:
                key = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as error:
                raise KmsError(f"密钥 {key_id} 不是合法 base64") from error
            keys[key_id] = key
        active = environ.get(ACTIVE_ENV, "").strip() or next(iter(keys))
        return cls(keys, active)

    @property
    def active_key_id(self) -> str:
        return self._active

    def encrypt(self, plaintext: bytes, *, aad: bytes = b"") -> Envelope:
        nonce = os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(self._keys[self._active]).encrypt(nonce, plaintext, aad)
        return Envelope(key_id=self._active, nonce=nonce, ciphertext=ciphertext)

    def decrypt(self, envelope: Envelope, *, aad: bytes = b"") -> bytes:
        key = self._keys.get(envelope.key_id)
        if key is None:
            raise KmsError(
                f"当前部署没有信封所用的密钥 {envelope.key_id}；请把旧密钥留在密钥环里再解压数据"
            )
        try:
            return AESGCM(key).decrypt(envelope.nonce, envelope.ciphertext, aad)
        except InvalidTag as error:
            raise KmsError("信封校验失败：密文被篡改，或附加数据不匹配") from error

    def reencrypt(self, envelope: Envelope, *, aad: bytes = b"") -> Envelope:
        """Move one record onto the active key without touching plaintext layout."""
        return self.encrypt(self.decrypt(envelope, aad=aad), aad=aad)
