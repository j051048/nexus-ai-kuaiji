"""Synthetic Supabase REST tests: private, immutable and shared across workers."""
import base64
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app.accounting.domain import AccountingError
from app.business.storage import AttachmentStore
from app.import_wizard.storage import ImportOriginalStore
from app.legacy_migration.storage import LegacyOriginalStore
from app.storage.factory import create_attachment_store
from app.storage.settings import StorageSettings


def png():
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(stream, format="PNG")
    return stream.getvalue()


class SyntheticStorage:
    def __init__(self):
        self.blobs, self.requests = {}, []
        self.public = False
        self.reply = None

    def handle(self, request):
        self.requests.append(request)
        if self.reply:
            answer = self.reply(request)
            if answer is not None:
                return answer
        path = request.url.path
        if path == "/storage/v1/bucket/accounting-originals":
            return httpx.Response(200, json={"id": "accounting-originals", "public": self.public, "file_size_limit": 10 * 1024 * 1024})
        prefix = "/storage/v1/object/accounting-originals/"
        if path.startswith(prefix):
            key = path[len(prefix):]
            if request.method == "POST":
                if key in self.blobs:
                    return httpx.Response(400, json={"error": "Duplicate"})
                assert request.headers["x-upsert"] == "false"
                self.blobs[key] = request.content
                return httpx.Response(200, json={"Key": "accounting-originals/" + key})
            if request.method == "GET":
                return httpx.Response(200, content=self.blobs[key]) if key in self.blobs else httpx.Response(404)
        if request.method == "DELETE" and path == "/storage/v1/object/accounting-originals":
            keys = json.loads(request.content)["prefixes"]
            for key in keys:
                self.blobs.pop(key, None)
            return httpx.Response(200, json=[])
        raise AssertionError((request.method, path))

    def store(self):
        # Two independently constructed stores, like API and worker containers,
        # share only the server object bytes; no path or in-process backend.
        return create_attachment_store(settings=StorageSettings(backend="supabase", supabase_url="https://synthetic-storage.example", bucket="accounting-originals", server_key="sb_secret_synthetic_test_only"),
                                       client=httpx.Client(transport=httpx.MockTransport(self.handle)))


@pytest.fixture
def cloud():
    return SyntheticStorage()


def test_api_and_independent_worker_share_originals_without_a_local_path(cloud):
    api, worker = cloud.store(), cloud.store()
    metadata = api.put("原始票据.png", "image/png", png())
    api.retain(metadata)
    assert api.root is worker.root is None
    assert worker.read(dict(metadata)) == png()
    assert not any("public/" in str(request.url) or "sign/" in str(request.url) for request in cloud.requests)
    assert "sb_secret_synthetic_test_only" not in repr(api.backend.settings)
    with pytest.raises(AccountingError, match="本地"):
        worker._path(metadata["storage_key"])


def test_all_original_types_use_same_backend_and_uuid_metadata(cloud):
    store, worker = cloud.store(), cloud.store()
    table_store = store.clone(ImportOriginalStore)
    csv_data = "交易日期,收入,支出,摘要\n2026-10-01,0,12.50,采购\n".encode()
    table, parsed = table_store.put("../流水.csv", csv_data)
    legacy = store.clone(LegacyOriginalStore).put("历史.json", b'{"records":[]}')
    assert table_store.backend is store.backend
    assert table["filename"] == "流水.csv" and parsed["headers"][0] == "交易日期"
    assert worker.read(table) == csv_data and worker.read(legacy) == b'{"records":[]}'
    for item in (table, legacy):
        assert len(item["id"]) == 32 and item["storage_key"] == item["id"][:2] + "/" + item["id"]
        assert len(item["sha256"]) == 64 and item["size_bytes"] > 0


def test_no_upload_overwrite_or_delete_on_uuid_collision(cloud, monkeypatch):
    import app.business.storage as module
    monkeypatch.setattr(module, "uuid4", lambda: SimpleNamespace(hex="1" * 32))
    store = cloud.store()
    item = store.put("一.png", "image/png", png())
    with pytest.raises(AccountingError):
        store.put("二.png", "image/png", png())
    assert store.read(item) == png()
    assert not any(request.method in {"PUT", "DELETE"} for request in cloud.requests)


def test_local_exclusive_create_collision_preserves_existing_bytes(tmp_path, monkeypatch):
    import app.business.storage as module
    monkeypatch.setattr(module, "uuid4", lambda: SimpleNamespace(hex="2" * 32))
    store = AttachmentStore(tmp_path)
    metadata = store.put("一.png", "image/png", png())
    with pytest.raises(AccountingError):
        store.put("二.png", "image/png", png())
    assert store.read(metadata) == png()


def test_discard_only_new_original_capability_and_never_persisted_or_retained(cloud):
    api, another_process = cloud.store(), cloud.store()
    item = api.put("合成.png", "image/png", png())
    api.discard(dict(item))
    another_process.discard(item)
    assert item["storage_key"] in cloud.blobs
    api.retain(item)
    api.discard(item)
    assert item["storage_key"] in cloud.blobs
    new = api.put("失败.png", "image/png", png())
    api.clone().discard(new)
    api.discard(new)
    assert new["storage_key"] not in cloud.blobs
    assert len([request for request in cloud.requests if request.method == "DELETE"]) == 1


def test_mutated_cleanup_metadata_cannot_delete_another_original(cloud):
    store = cloud.store()
    old, new = store.put("一.png", "image/png", png()), store.put("二.png", "image/png", png())
    new["storage_key"] = old["storage_key"]
    with pytest.raises(AccountingError, match="清理范围"):
        store.discard(new)
    assert len(cloud.blobs) == 2


@pytest.mark.parametrize("public", [True, None, "false", 0])
def test_bucket_must_be_strictly_private(cloud, public):
    cloud.public = public
    with pytest.raises(AccountingError, match="私有"):
        cloud.store().put("一.png", "image/png", png())
    assert not cloud.blobs and len(cloud.requests) == 1


def test_public_bucket_change_stops_reads_before_original_download(cloud):
    store = cloud.store()
    item = store.put("合成.png", "image/png", png())
    cloud.public = True
    before = len(cloud.requests)
    with pytest.raises(AccountingError, match="私有"):
        store.read(item)
    assert len(cloud.requests) == before + 1


@pytest.mark.parametrize("change", ["bytes", "size", "sha", "oversized"])
def test_download_integrity_and_bounds(cloud, change):
    store = cloud.store()
    item = store.put("一.png", "image/png", png())
    if change == "bytes":
        cloud.blobs[item["storage_key"]] = b"corrupt"
    elif change == "size":
        item["size_bytes"] += 1
    elif change == "sha":
        item["sha256"] = "0" * 64
    else:
        cloud.reply = lambda request: httpx.Response(200, headers={"Content-Length": str(10 * 1024 * 1024 + 1)}, content=b"") if "/object/" in request.url.path else None
    with pytest.raises(AccountingError):
        store.read(item)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_never_forwards_server_key(cloud, status):
    cloud.reply = lambda request: httpx.Response(status, headers={"Location": "https://other.example/steal"})
    with pytest.raises(AccountingError):
        cloud.store().verify_private()
    assert len(cloud.requests) == 1
    assert cloud.requests[0].url.host == "synthetic-storage.example"


def test_unknown_upload_result_keeps_object_without_retry_or_cleanup(cloud):
    def lost_ack(request):
        if request.method == "POST":
            cloud.blobs[request.url.path.rsplit("/object/accounting-originals/", 1)[-1]] = request.content
            raise httpx.ReadTimeout("synthetic lost acknowledgement", request=request)
    cloud.reply = lost_ack
    with pytest.raises(AccountingError, match="结果暂不确定"):
        cloud.store().put("一.png", "image/png", png())
    assert len(cloud.blobs) == 1
    assert len([request for request in cloud.requests if request.method == "POST"]) == 1
    assert not any(request.method == "DELETE" for request in cloud.requests)


def test_error_body_is_not_exposed(cloud):
    cloud.reply = lambda request: httpx.Response(401, json={"debug": "sb_secret_should_never_be_echoed"})
    with pytest.raises(AccountingError) as error:
        cloud.store().verify_private()
    assert "sb_secret" not in str(error.value)


def test_current_secret_key_uses_apikey_only(cloud):
    cloud.store().verify_private()
    assert cloud.requests[0].headers["apikey"].startswith("sb_secret_")
    assert "Authorization" not in cloud.requests[0].headers


def test_legacy_service_role_key_uses_bearer_and_apikey(cloud):
    payload = base64.urlsafe_b64encode(b'{"role":"service_role"}').decode().rstrip("=")
    key = "synthetic." + payload + ".signature"
    settings = StorageSettings(backend="supabase", supabase_url="https://synthetic-storage.example", bucket="accounting-originals", server_key=key)
    create_attachment_store(settings=settings, client=httpx.Client(transport=httpx.MockTransport(cloud.handle))).verify_private()
    assert cloud.requests[0].headers["Authorization"] == "Bearer " + key


@pytest.mark.parametrize("url", ["http://project.supabase.co", "https://user:pass@project.supabase.co", "https://project.supabase.co/storage/v1", "https://project.supabase.co/?key=x", "https://project.supabase.co/#x", "https://project.supabase.co\\other"])
def test_endpoint_credentials_path_or_insecure_url_rejected(url):
    with pytest.raises(ValueError):
        StorageSettings(backend="supabase", supabase_url=url, bucket="accounting-originals", server_key="sb_secret_synthetic")


@pytest.mark.parametrize("key", ["", "sb_publishable_synthetic", "synthetic.e30.signature", "sb_secret_contains\nnewline"])
def test_storage_rejects_browser_or_invalid_key(key):
    with pytest.raises(ValueError):
        StorageSettings(backend="supabase", supabase_url="https://project.supabase.co", bucket="accounting-originals", server_key=key)


def test_storage_factory_env_contract_no_remote_disk_and_no_secret_repr(monkeypatch):
    values = {"ACCOUNTING_STORAGE_BACKEND": "supabase", "ACCOUNTING_STORAGE_SUPABASE_URL": "http://127.0.0.1:54321", "ACCOUNTING_STORAGE_ALLOW_HTTP": "true", "ACCOUNTING_STORAGE_BUCKET": "accounting-originals", "ACCOUNTING_STORAGE_SERVER_KEY": "sb_secret_synthetic_test"}
    settings = StorageSettings.from_env(values)
    assert settings.allow_http and "sb_secret" not in repr(settings)
    store = create_attachment_store(local_root="must-not-create-this-directory", settings=settings)
    assert store.root is None
    with pytest.raises(ValueError):
        StorageSettings.from_env({**values, "ACCOUNTING_STORAGE_ALLOW_HTTP": "false"})


@pytest.mark.parametrize("field,value", [("backend", "s3"), ("timeout_seconds", 0), ("timeout_seconds", 61), ("bucket", "../outside")])
def test_invalid_settings_rejected(field, value):
    kwargs = dict(backend="supabase", supabase_url="https://project.supabase.co", bucket="accounting-originals", server_key="sb_secret_synthetic")
    kwargs[field] = value
    with pytest.raises(ValueError):
        StorageSettings(**kwargs)
