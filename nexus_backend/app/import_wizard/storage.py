import re
from ..business.storage import AttachmentStore
from ..accounting.domain import AccountingError
from .parser import parse_file


class ImportOriginalStore(AttachmentStore):
    def put(self, filename, data):
        filename = re.sub(r'[\x00-\x1f\x7f]', '', (filename or '').replace('\\', '/').split('/')[-1]).strip()
        if not 1 <= len(filename) <= 150:
            raise AccountingError('文件名称须为1–150字', 422)
        table = parse_file(filename, data)
        media_type = 'text/csv' if filename.lower().endswith('.csv') else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        return self.put_original(filename, media_type, data), table
