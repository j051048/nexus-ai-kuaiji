"""Set fleet ceilings explicitly with the migration administrator connection.

Example: --processing 4 --provider-global 3 --provider-default 2
Optional --supplier api.example.com=1 (repeatable). No secrets in CLI arguments.
"""

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.migrate_accounting import admin_connection, require_admin  # noqa: E402


def configure(url, processing, provider_global, provider_default, suppliers=()):
    values = (processing, provider_global, provider_default)
    if any(type(v) is not int or not 1 <= v <= 8 for v in values):
        raise ValueError("处理与供应商上限必须为 1–8 的整数")
    parsed = []
    for item in suppliers:
        match = re.fullmatch(r"([a-z0-9.-]{1,253})=([1-8])", item)
        if not match:
            raise ValueError("供应商限制格式为 hostname=1–8，不接受地址、密钥或模型名称")
        parsed.append((match[1], int(match[2])))
    with admin_connection(url) as conn:
        require_admin(conn)
        with conn.transaction():
            # Same lock order as claim/transport acquisition. Existing work is
            # allowed to finish; lowering limits blocks only new acquisitions.
            conn.execute("SELECT pg_advisory_xact_lock_shared(6420031786281903101)")
            conn.execute("SELECT pg_advisory_xact_lock(6420031786281903102)")
            conn.execute("SELECT pg_advisory_xact_lock(6420031786281903103)")
            conn.execute(
                "UPDATE accounting.processing_runtime_limits SET processing_global_limit=%s,provider_global_limit=%s,provider_default_limit=%s WHERE singleton=1",
                values,
            )
            for supplier in parsed:
                conn.execute(
                    "INSERT INTO accounting.provider_runtime_limits(provider,max_inflight) VALUES(%s,%s) ON CONFLICT(provider) DO UPDATE SET max_inflight=excluded.max_inflight",
                    supplier,
                )
    return {
        "processing_global_limit": processing,
        "provider_global_limit": provider_global,
        "provider_default_limit": provider_default,
        "supplier_limits": dict(parsed),
    }


def main():
    parser = argparse.ArgumentParser(description="显式设置会计后台跨进程处理容量")
    parser.add_argument("--processing", type=int, required=True)
    parser.add_argument("--provider-global", type=int, required=True)
    parser.add_argument("--provider-default", type=int, required=True)
    parser.add_argument("--supplier", action="append", default=[])
    args = parser.parse_args()
    try:
        result = configure(
            os.getenv("ACCOUNTING_ADMIN_DATABASE_URL"),
            args.processing,
            args.provider_global,
            args.provider_default,
            args.supplier,
        )
    except Exception:
        raise SystemExit(
            "容量设置未完成，请检查管理员连接、迁移和 1–8 边界；未显示连接信息"
        ) from None
    print(result)


if __name__ == "__main__":
    main()
