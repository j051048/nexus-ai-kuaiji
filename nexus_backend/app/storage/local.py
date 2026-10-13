"""Exclusive local files for development and single persistent hosts."""

import os
import re
from pathlib import Path

from ..accounting.domain import AccountingError

KEY_PATTERN = re.compile(r"[0-9a-f]{2}/[0-9a-f]{32}\Z")


def check_key(key):
    if not isinstance(key, str) or not KEY_PATTERN.fullmatch(key):
        raise AccountingError("附件存储记录无效", 503)


class LocalBlobBackend:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, key):
        check_key(key)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise AccountingError("附件存储记录无效", 503)
        return path

    def put(self, key, data, media_type):
        path = self.path(key)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        created = False
        try:
            with path.open("xb") as stream:
                created = True
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            path.chmod(0o600)
        except OSError:
            # An improbable UUID collision must never unlink an existing blob.
            if created:
                path.unlink(missing_ok=True)
            raise AccountingError("附件存储暂时不可用，请重试", 503) from None

    def read(self, key, max_bytes):
        try:
            with self.path(key).open("rb") as stream:
                return stream.read(max_bytes + 1)
        except OSError:
            raise AccountingError("附件原件暂不可用，请联系管理员恢复", 503) from None

    def discard(self, key):
        self.path(key).unlink(missing_ok=True)
