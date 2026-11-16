"""Read-only FDE adapter and entity-tag registry.

This is a projection over existing adapters and master data. It does not
enable a live provider, write a mapping, or grant any accounting action.
"""
from __future__ import annotations

import hashlib
import json

from ..accounting.access_policy import AccessControl
from ..accounting.service import now


ADAPTERS = [
    {"code": "tax_xlsx", "label": "税务来源台账（XLSX）", "state": "available", "live_external": False},
    {"code": "transport_master", "label": "车辆/驾驶员/项目主数据", "state": "available", "live_external": False},
    {"code": "oa_snapshot", "label": "OA 审批快照（人工核定）", "state": "available", "live_external": False},
    {"code": "lease_contract", "label": "租赁合同与收入计划", "state": "available", "live_external": False},
    {"code": "tplus54_synthetic", "label": "T+ 54 列合成适配", "state": "available", "live_external": False, "native_format": False},
    {"code": "sync_stub", "label": "文件链同步替身", "state": "available", "live_external": False},
]


def _contract(adapter: dict) -> dict:
    descriptor = {
        "code": adapter["code"],
        "label": adapter["label"],
        "live_external": bool(adapter.get("live_external")),
        "native_format": bool(adapter.get("native_format", True)),
        "contract_version": "v1",
    }
    digest = hashlib.sha256(
        json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **adapter,
        "contract_version": "v1",
        "contract_sha256": digest,
        "certification": "local_contract_only",
    }


class FdeAdapterService:
    def __init__(self, db, access: AccessControl | None = None):
        self.db = db
        self.access = access or AccessControl()

    def catalog(self, actor, company_id: str):
        with self.db.transaction(actor=actor) as conn:
            self.access.company(conn, actor, company_id, manage=True)
            def count(sql, *params):
                return int(conn.execute(sql, params).fetchone()[0])
            tags = {
                "vehicles": count(
                    "SELECT COUNT(*) FROM transport_subjects WHERE firm_id=? AND company_id=? AND kind='vehicle'",
                    actor.firm_id, company_id,
                ),
                "drivers": count(
                    "SELECT COUNT(*) FROM transport_subjects WHERE firm_id=? AND company_id=? AND kind='driver'",
                    actor.firm_id, company_id,
                ),
                "projects": count(
                    "SELECT COUNT(*) FROM transport_subjects WHERE firm_id=? AND company_id=? AND kind='project'",
                    actor.firm_id, company_id,
                ),
                "aliases": count(
                    "SELECT COUNT(*) FROM transport_aliases a JOIN transport_subjects s ON s.id=a.subject_id "
                    "WHERE s.firm_id=? AND s.company_id=?",
                    actor.firm_id, company_id,
                ),
                "tax_sources": count(
                    "SELECT COUNT(*) FROM tax_source_files WHERE firm_id=? AND company_id=?",
                    actor.firm_id, company_id,
                ),
                "oa_snapshots": count(
                    "SELECT COUNT(*) FROM oa_approval_snapshots WHERE firm_id=? AND company_id=?",
                    actor.firm_id, company_id,
                ),
            }
            adapters = [_contract(item) for item in ADAPTERS]
            fingerprint = hashlib.sha256(
                json.dumps(
                    [(item["code"], item["contract_sha256"]) for item in adapters],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            return {
                "company_id": company_id,
                "adapters": adapters,
                "catalog_fingerprint": fingerprint,
                "entity_tags": tags,
                "live_external": False,
                "note": "只读目录；适配器可用不等于客户真实接口已接通，也不授予写入或过账权限。",
                "generated_at": now(),
            }
