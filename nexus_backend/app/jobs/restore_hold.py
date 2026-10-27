"""Fail closed while a restored copy is under review.

The explicit marker remains supported, but a restored SQLite/attachment target
is also detected from the database/attachment path. A worker or API pointed at
a restored directory must not silently start just because an operator forgot
to set an environment variable.
"""

import os
from pathlib import Path

MARKER_NAMES = ("restore-verification.json", "restore-hold.json")


def _candidate_directories(database_path=None, attachment_root=None):
    values = []
    if database_path:
        path = Path(database_path)
        values.append(path.parent if path.suffix or path.is_file() else path)
    if attachment_root:
        values.append(Path(attachment_root).parent)
    for key in (
        "ACCOUNTING_DB_PATH",
        "ACCOUNTING_DATABASE_PATH",
        "ACCOUNTING_ATTACHMENT_ROOT",
        "ACCOUNTING_STORAGE_ROOT",
    ):
        value = os.environ.get(key, "").strip()
        if value:
            path = Path(value)
            values.append(path.parent if key.endswith("_PATH") else path)
    values.append(Path.cwd())
    return values


def _has_marker(directory):
    try:
        return any((Path(directory) / name).is_file() for name in MARKER_NAMES)
    except OSError:
        return False


def restore_hold_active(database_path=None, attachment_root=None):
    """Boolean form for callers that must refuse one action instead of exiting."""
    if os.environ.get("ACCOUNTING_RESTORE_HOLD_FILE", "").strip():
        return True
    return any(
        _has_marker(directory)
        for directory in _candidate_directories(database_path, attachment_root)
    )


def enforce_restore_hold(database_path=None, attachment_root=None):
    explicit = os.environ.get("ACCOUNTING_RESTORE_HOLD_FILE", "").strip()
    if explicit:
        if not Path(explicit).is_file():
            raise SystemExit("已配置恢复隔离标记但文件不存在，拒绝启动；请修复标记或移除该配置")
        raise SystemExit(
            "恢复环境处于隔离核对状态，禁止启动会外发的 worker/API；"
            "请先核对在途任务与结果未知状态，再移除恢复隔离标记"
        )
    for directory in _candidate_directories(database_path, attachment_root):
        if _has_marker(directory):
            raise SystemExit(
                "检测到恢复验证标记，当前数据库/原件目录属于隔离恢复副本；"
                "禁止启动正常 API/worker，请先核对在途任务与结果未知状态"
            )
    return True
