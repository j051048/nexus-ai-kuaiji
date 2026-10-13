"""Immutable migration originals using the existing private blob namespace."""
import re

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
        return self.put_original(name, mime, data)
