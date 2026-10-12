"""Private, immutable attachment blobs. Metadata and authorization live in DB."""
import hashlib
import io
import os
import re
import warnings
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from starlette.exceptions import HTTPException

from ..accounting.domain import AccountingError

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_FILE_BYTES + 128 * 1024
MAX_IMAGE_PIXELS = 20_000_000
KEY_PATTERN = re.compile(r"[0-9a-f]{2}/[0-9a-f]{32}\Z")
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


class AttachmentStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, key):
        if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
            raise AccountingError("附件存储记录无效", 503)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise AccountingError("附件存储记录无效", 503)
        return path

    def put(self, filename, content_type, data):
        name, detected = validate_attachment(filename, content_type, data)
        blob_id = uuid4().hex
        key = f"{blob_id[:2]}/{blob_id}"
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with path.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            path.chmod(0o600)
        except OSError:
            if path.exists():
                path.unlink()
            raise AccountingError("附件存储暂时不可用，请重试", 503) from None
        return {"id": blob_id, "storage_key": key, "filename": name, "media_type": detected,
                "sha256": hashlib.sha256(data).hexdigest(), "size_bytes": len(data)}

    def discard(self, metadata):
        # Only the newly generated blob from a failed transaction is discarded.
        self._path(metadata["storage_key"]).unlink(missing_ok=True)

    def read(self, metadata):
        path = self._path(metadata["storage_key"])
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_FILE_BYTES + 1)
        except OSError:
            raise AccountingError("附件原件暂不可用，请联系管理员恢复", 503) from None
        if len(data) != metadata["size_bytes"] or hashlib.sha256(data).hexdigest() != metadata["sha256"]:
            raise AccountingError("附件完整性校验失败，请联系管理员恢复原件", 503)
        return data


class AttachmentBodyLimitMiddleware:
    """Bound multipart bytes even for requests without Content-Length."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        limited = scope["type"] == "http" and scope.get("method") == "POST" and re.fullmatch(r"/api/business/events/[^/]+/attachments", scope.get("path", ""))
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
