import hashlib
import os
import re
from uuid import uuid4
from ..business.storage import AttachmentStore
from ..accounting.domain import AccountingError
from .parser import parse_file


class ImportOriginalStore(AttachmentStore):
    def put(self, filename, data):
        filename = re.sub(r'[\x00-\x1f\x7f]', '', (filename or '').replace('\\', '/').split('/')[-1]).strip()
        if not 1 <= len(filename) <= 150:
            raise AccountingError('文件名称须为1–150字', 422)
        table = parse_file(filename, data)
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
            raise AccountingError('导入原件保存失败，请稍后重试', 503) from None
        return {'id': identifier, 'storage_key': key, 'filename': filename, 'size_bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest(), 'media_type': 'text/csv' if filename.lower().endswith('.csv') else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}, table
