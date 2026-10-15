"""Materialize processing defaults; missing settings still fail closed elsewhere.

This does not enable scheduling automation, change model budgets, or retry old
work. An explicit administrator pause always takes precedence over the default.
"""

import json
from datetime import datetime, timezone

from .access import AccessControl


def ensure_company_ai_default(conn, actor, company_id, *, source="company_create"):
    """Enable an unconfigured active company in the caller's write transaction.

    Call after creating/reactivating its firm-client association. The current
    manager is the audit actor; existing enabled=0 or enabled=1 rows stay intact.
    """
    AccessControl().company(conn, actor, company_id, manage=True)
    timestamp = datetime.now(timezone.utc).isoformat()
    inserted = conn.execute(
        "INSERT INTO ai_company_settings(firm_id,company_id,enabled,updated_by,updated_at) "
        "VALUES(?,?,1,?,?) ON CONFLICT(firm_id,company_id) DO NOTHING",
        (actor.firm_id, company_id, actor.user_id, timestamp),
    )
    if inserted.rowcount != 1:
        return False
    conn.execute(
        "INSERT INTO audit_events(firm_id,user_id,action,object_id,details,created_at,company_id) "
        "VALUES(?,?,'admin.ai.default_enabled',?,?,?,?)",
        (
            actor.firm_id,
            actor.user_id,
            company_id,
            json.dumps(
                {
                    "company_id": company_id,
                    "enabled": True,
                    "source": source,
                    "reason": "未配置企业按产品默认启用AI处理；仍受供应商、配额及人工复核约束",
                },
                ensure_ascii=False,
            ),
            timestamp,
            company_id,
        ),
    )
    return True
