"""Construct the same backend for API, OCR, chat and migration processes."""

import os

from .settings import StorageSettings
from .supabase import SupabaseBlobBackend


def create_attachment_store(local_root=None, settings=None, client=None):
    from ..business.storage import AttachmentStore

    settings = settings or StorageSettings.from_env()
    if settings.backend == "supabase":
        return AttachmentStore(backend=SupabaseBlobBackend(settings, client=client))
    root = local_root or os.environ.get("ACCOUNTING_ATTACHMENT_ROOT")
    if not root:
        raise ValueError("local 存储须配置 ACCOUNTING_ATTACHMENT_ROOT 或传入持久目录")
    return AttachmentStore(root)
