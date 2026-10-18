"""Refuse to start outbound-capable workers while a restored copy is under review."""

import os
from pathlib import Path


def enforce_restore_hold():
    """Fail closed when the operator points the process at a restore hold file.

    The restore command writes ``restore-verification.json`` with outbound
    disabled. Operators must set ``ACCOUNTING_RESTORE_HOLD_FILE`` to that path
    (or another hold marker) until in-flight and unknown-result jobs have been
    reconciled. This is deliberately an explicit opt-in: a normal worker start
    is not silently blocked by a stale file in an unrelated directory.
    """
    value = os.environ.get("ACCOUNTING_RESTORE_HOLD_FILE", "").strip()
    if not value:
        return
    path = Path(value)
    if path.is_file():
        raise SystemExit(
            "恢复环境处于隔离核对状态，禁止启动会外发的 worker；"
            "请先核对在途任务与结果未知状态，再移除 ACCOUNTING_RESTORE_HOLD_FILE"
        )
