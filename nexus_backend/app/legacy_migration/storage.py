"""Immutable migration originals using the existing private blob namespace."""
import hashlib
import os
import re
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..business.storage import AttachmentStore
from .normalization import MAX_BYTES


class LegacyOriginalStore(AttachmentStore):
    def put(self, filename, data):
        name = re.sub(r'[\x00-\x1f\x7f]', '', (filename or '').replace('\\', '/').split('/')[-1]).strip()
        if not 1 <= len(name) <= 150:
            raise AccountingError('旧账原件名称须为 1–150 字', 422)
        if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
            raise AccountingError('旧账原件不能为空，单文件最多 10 MB', 413)
        extension = name.lower().rsplit('.', 1)[-1]
        mime = {'json': 'application/json', 'csv': 'text/csv', 'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}.get(extension)
        if not mime or (extension == 'xlsx' and not data.startswith(b'PK')):
            raise AccountingError('支持中性 JSON、CSV、.xlsx；不支持厂商备份文件', 422)
        identifier = uuid4().hex
        key = identifier[:2] + '/' + identifier
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with path.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            path.chmod(0o600)
        except OSError:
            path.unlink(missing_ok=True)
            raise AccountingError('旧账原件保存失败，请稍后重试', 503) from None
        return {'id': identifier, 'storage_key': key, 'filename': name, 'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'media_type': mime}
