"""Private, immutable attachment blobs. Metadata and authorization live in DB."""
import hashlib
import io
import re
import warnings
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from starlette.exceptions import HTTPException

from ..accounting.domain import AccountingError
from ..storage.local import KEY_PATTERN, LocalBlobBackend, check_key

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_FILE_BYTES + 128 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MIME_EXTENSIONS = {"application/pdf": {".pdf"}, "image/png": {".png"}, "image/jpeg": {".jpg", ".jpeg"}, "image/webp": {".webp"}}


def validate_attachment(filename, claimed_type, data):
    name = re.sub(r"[\x00-\x1f\x7f]", "", (filename or "").replace("\\", "/").split("/")[-1]).strip()
    if not name or len(name) > 150:
        raise AccountingError("附件名称须为 1–150 个字符", 422)
    if not data or len(data) > MAX_FILE_BYTES:
        raise AccountingError("附件不能为空，单个文件最多 10 MB", 413)
    if data.startswith(b"%PDF-") and b"%%EOF" in data[-2048:]:
        detected = "application/pdf"
    else:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as picture:
                    detected = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}.get(picture.format)
                    if picture.width * picture.height > MAX_IMAGE_PIXELS:
                        raise AccountingError("图片尺寸过大，请压缩后上传", 413)
                    picture.verify()
        except AccountingError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            raise AccountingError("附件内容无法识别，请上传 PDF、PNG、JPEG 或 WebP", 422) from None
    if detected not in MIME_EXTENSIONS or Path(name).suffix.lower() not in MIME_EXTENSIONS[detected]:
        raise AccountingError("附件扩展名与实际内容不符，仅支持 PDF、PNG、JPEG、WebP", 422)
    if claimed_type and claimed_type not in {detected, "application/octet-stream"}:
        raise AccountingError("附件声明格式与实际内容不符", 422)
    return name, detected


class StoredOriginal(dict):
    """Ephemeral cleanup capability; never reconstructed from a database row."""
    def __init__(self, metadata, backend):
        super().__init__(metadata)
        self._backend = backend
        self._created_key = metadata["storage_key"]
        self._discardable = True


def retain_original(metadata):
    """Protect a blob before COMMIT: uncertain acknowledgements retain evidence."""
    if isinstance(metadata, StoredOriginal):
        metadata._discardable = False


class AttachmentStore:
    def __init__(self, root=None, *, backend=None):
        if backend is None and root is None:
            raise ValueError("附件存储须配置本地持久目录或共享存储后端")
        self.backend = backend if backend is not None else LocalBlobBackend(root)
        self.root = self.backend.root

    def clone(self, store_type=None):
        return (store_type or type(self))(backend=self.backend)

    def verify_private(self):
        if hasattr(self.backend, "verify_private"):
            return self.backend.verify_private()
        return {"private": True, "backend": "local"}

    def _path(self, key):
        if not isinstance(self.backend, LocalBlobBackend):
            raise AccountingError("共享对象存储没有可读取的本地原件路径", 503)
        return self.backend.path(key)

    def put_original(self, name, detected, data):
        if not isinstance(data, bytes) or not data or len(data) > MAX_FILE_BYTES:
            raise AccountingError("附件不能为空，单个文件最多 10 MB", 413)
        blob_id = uuid4().hex
        key = f"{blob_id[:2]}/{blob_id}"
        self.backend.put(key, data, detected)
        return StoredOriginal({"id": blob_id, "storage_key": key, "filename": name, "media_type": detected,
                               "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}, self.backend)

    def put(self, filename, content_type, data):
        name, detected = validate_attachment(filename, content_type, data)
        return self.put_original(name, detected, data)

    def retain(self, metadata):
        retain_original(metadata)

    def discard(self, metadata):
        # A persisted metadata row (or a clone in another process) carries no
        # deletion capability. Never remove evidence after an uncertain COMMIT.
        if not isinstance(metadata, StoredOriginal) or metadata._backend is not self.backend or not metadata._discardable:
            return
        if metadata.get("storage_key") != metadata._created_key:
            raise AccountingError("新上传原件的清理范围无效", 503)
        metadata._discardable = False
        self.backend.discard(metadata._created_key)

    def read(self, metadata):
        metadata = dict(metadata)
        check_key(metadata["storage_key"])
        size, digest = metadata.get("size_bytes"), metadata.get("sha256")
        if type(size) is not int or not 1 <= size <= MAX_FILE_BYTES or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise AccountingError("附件存储记录无效", 503)
        data = self.backend.read(metadata["storage_key"], size)
        if len(data) != metadata["size_bytes"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise AccountingError("附件完整性校验失败，请联系管理员恢复原件", 503)
        return data


class AttachmentBodyLimitMiddleware:
    """Bound multipart bytes even for requests without Content-Length."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limited = scope["type"] == "http" and scope.get("method") == "POST" and re.fullmatch(r"/api/(?:business/events|intake/drafts)/[^/]+/attachments", scope.get("path", ""))
        if not limited:
            return await self.app(scope, receive, send)
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > MAX_REQUEST_BYTES:
                    raise HTTPException(status_code=413, detail="上传请求过大，单个附件最多 10 MB")
            return message

        await self.app(scope, bounded_receive, send)
