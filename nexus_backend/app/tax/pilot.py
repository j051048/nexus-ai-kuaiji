"""Versioned pilot boundary and dependency ledger.

The ledger deliberately separates *what the client has confirmed* from what a
preview may assume.  A profile is immutable once confirmed; changing a boundary
creates a new draft version, and confirming it invalidates every unfinished
preview that was produced from an older version.
"""

from __future__ import annotations

import hashlib
import json
import re
from uuid import uuid4

from ..accounting.domain import AccountingError
from ..accounting.service import now

AUTHORITY_MODES = {"internal", "tplus_voucher", "hybrid"}
DEPENDENCIES = (
    ("income_336_335", "336/335 行收入明细逐行参与/排除"),
    ("prior_period_residue", "旧期间残留行处理"),
    ("imbalance_832_19", "832.19 摊销不平来源"),
    ("rent_3550", "租赁收入 3,550 差额"),
    ("insurance_formula", "保险月摊算法与取整"),
    ("tax_base_5990", "5,990 计税基数出处"),
    ("tax_rounding", "税额分位差处理"),
    ("tplus_template", "T+ 版本、54 列模板与回执"),
    ("source_files", "完整月源文件与相邻期"),
    ("payroll_scope", "工资/社保数据范围"),
)
DEPENDENCY_LABELS = dict(DEPENDENCIES)
REQUIRED_CODES = tuple(code for code, _ in DEPENDENCIES)
CONFIG_KEYS = {
    "authority_mode",
    "source_of_truth",
    "tplus_version",
    "template_sha256",
    "voucher_word",
    "numbering_owner",
    "reporting_mode",
    "notes",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _config(payload):
    unknown = set(payload) - CONFIG_KEYS
    if unknown:
        raise AccountingError("未知的试点配置字段: " + ",".join(sorted(unknown)), 422)
    mode = payload.get("authority_mode")
    if mode not in AUTHORITY_MODES:
        raise AccountingError("必须确认内部核算、T+ 凭证交付或混合模式", 422)
    if mode in {"tplus_voucher", "hybrid"}:
        version = str(payload.get("tplus_version") or "").strip()
        digest = str(payload.get("template_sha256") or "").strip().lower()
        if not version:
            raise AccountingError("T+ 模式必须登记实际产品版本", 422)
        if not HEX64.fullmatch(digest):
            raise AccountingError("T+ 模式必须登记原生模板 SHA-256", 422)
        if payload.get("voucher_word") not in {"记", "转", "收", "付"}:
            raise AccountingError("T+ 模式必须确认凭证字号", 422)
    return dict(payload)


def _dependencies(payload):
    if not isinstance(payload, list):
        raise AccountingError("资料依赖清单格式无效", 422)
    result = {}
    for item in payload:
        code = item.get("code")
        if code not in DEPENDENCY_LABELS:
            raise AccountingError("未知的资料依赖项: " + str(code), 422)
        status = item.get("status")
        if status not in {"unresolved", "confirmed", "not_applicable"}:
            raise AccountingError("资料依赖状态无效", 422)
        evidence = str(item.get("evidence_ref") or "").strip()
        if status in {"confirmed", "not_applicable"} and not evidence:
            raise AccountingError(f"{DEPENDENCY_LABELS[code]} 必须有来源证据", 422)
        result[code] = {
            "code": code,
            "label": DEPENDENCY_LABELS[code],
            "required": True,
            "status": status,
            "owner_role": str(item.get("owner_role") or "客户会计")[:80],
            "evidence_ref": evidence[:500],
            "impact": str(item.get("impact") or "")[:500],
            "note": str(item.get("note") or "")[:1000],
        }
    missing = [code for code in REQUIRED_CODES if code not in result]
    if missing:
        raise AccountingError("资料依赖清单缺少: " + ",".join(missing), 422)
    return result


def _present_profile(row, dependencies=(), previews=(), active=None):
    return {
        "id": row["id"],
        "company_id": row["company_id"],
        "version": int(row["version"]),
        "status": row["status"],
        "authority_mode": row["authority_mode"],
        "config": json.loads(row["config_json"]),
        "fingerprint": row["fingerprint"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "confirmed_by": row["confirmed_by"],
        "confirmed_at": row["confirmed_at"],
        "dependencies": list(dependencies),
        "previews": list(previews),
        "active": bool(active and row["id"] == active["id"]),
    }


class PilotBoundaryService:
    def __init__(self, db):
        from ..accounting.access import AccessControl

        self.db = db
        self.access = AccessControl()

    def _company(self, conn, actor, company_id, manage=False):
        company = self.access.company(conn, actor, company_id, manage=manage)
        return company

    def _latest(self, conn, company_id, status=None):
        sql = "SELECT * FROM tax_pilot_profiles WHERE company_id=?"
        args = [company_id]
        if status:
            sql += " AND status=?"
            args.append(status)
        return conn.execute(sql + " ORDER BY version DESC LIMIT 1", tuple(args)).fetchone()

    @staticmethod
    def _deps(conn, profile_id):
        rows = conn.execute(
            "SELECT * FROM tax_pilot_dependencies WHERE profile_id=? ORDER BY code", (profile_id,)
        ).fetchall()
        return [
            {
                "code": row["code"],
                "label": row["label"],
                "required": bool(row["required"]),
                "status": row["status"],
                "owner_role": row["owner_role"],
                "evidence_ref": row["evidence_ref"],
                "impact": row["impact"],
                "note": row["note"],
            }
            for row in rows
        ]

    @staticmethod
    def _events(conn, profile_id):
        rows = conn.execute(
            "SELECT id,purpose,created_by,created_at,invalidated_at FROM tax_pilot_previews "
            "WHERE profile_id=? ORDER BY created_at DESC",
            (profile_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def readiness(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self._company(conn, actor, company_id)
            confirmed = self._latest(conn, company_id, "confirmed")
            draft = self._latest(conn, company_id, "draft")
            target = draft or confirmed
            if target is None:
                return {
                    "company_id": company_id,
                    "exists": False,
                    "authority_mode": "",
                    "active_profile_id": None,
                    "draft_profile_id": None,
                    "blockers": [
                        {"code": code, "label": DEPENDENCY_LABELS[code]} for code in REQUIRED_CODES
                    ],
                    "dependencies": [],
                    "next_action": "登记客户边界、来源与资料依赖",
                }
            deps = self._deps(conn, target["id"])
            blockers = [
                {"code": item["code"], "label": item["label"], "status": item["status"]}
                for item in deps
                if item["required"] and item["status"] == "unresolved"
            ]
            return {
                "company_id": company_id,
                "exists": True,
                "authority_mode": target["authority_mode"],
                "active_profile_id": confirmed["id"] if confirmed else None,
                "draft_profile_id": draft["id"] if draft else None,
                "target": _present_profile(
                    target, deps, self._events(conn, target["id"]), confirmed
                ),
                "blockers": blockers,
                "next_action": "补齐客户资料并确认试点配置" if blockers else "可确认试点配置",
            }

    def update(self, actor, company_id, expected_version, config, dependencies):
        next_config = _config(config)
        deps = _dependencies(dependencies)
        with self.db.transaction(write=True, actor=actor) as conn:
            company = self._company(conn, actor, company_id, manage=True)
            latest = self._latest(conn, company_id)
            current = int(latest["version"]) if latest else 0
            if current != expected_version:
                raise AccountingError("试点配置版本已变化，请刷新后重试", 409)
            version = current + 1
            if latest and latest["status"] == "draft":
                conn.execute(
                    "UPDATE tax_pilot_profiles SET status='superseded' WHERE id=?", (latest["id"],)
                )
            conn.execute(
                "UPDATE tax_pilot_previews SET invalidated_at=? WHERE invalidated_at IS NULL "
                "AND profile_id IN (SELECT id FROM tax_pilot_profiles WHERE company_id=?)",
                (now(), company_id),
            )
            payload = {"config": next_config, "dependencies": list(deps.values())}
            fingerprint = _fingerprint(payload)
            profile_id = uuid4().hex
            conn.execute(
                "INSERT INTO tax_pilot_profiles(id,firm_id,company_id,version,status,authority_mode,config_json,fingerprint,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    profile_id,
                    actor.firm_id,
                    company_id,
                    version,
                    "draft",
                    next_config["authority_mode"],
                    _canonical(next_config),
                    fingerprint,
                    actor.user_id,
                    now(),
                ),
            )
            for item in deps.values():
                conn.execute(
                    "INSERT INTO tax_pilot_dependencies(id,profile_id,code,label,required,status,owner_role,evidence_ref,impact,note,updated_by,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        uuid4().hex,
                        profile_id,
                        item["code"],
                        item["label"],
                        1,
                        item["status"],
                        item["owner_role"],
                        item["evidence_ref"],
                        item["impact"],
                        item["note"],
                        actor.user_id,
                        now(),
                    ),
                )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    actor.firm_id,
                    None,
                    actor.user_id,
                    "tax.pilot.draft",
                    profile_id,
                    _canonical(
                        {"company_id": company_id, "version": version, "fingerprint": fingerprint}
                    ),
                    now(),
                ),
            )
            profile = conn.execute(
                "SELECT * FROM tax_pilot_profiles WHERE id=?", (profile_id,)
            ).fetchone()
            return _present_profile(profile, list(deps.values()), [], None)

    def confirm(self, actor, company_id, expected_version):
        with self.db.transaction(write=True, actor=actor) as conn:
            self._company(conn, actor, company_id, manage=True)
            draft = self._latest(conn, company_id, "draft")
            if not draft or int(draft["version"]) != expected_version:
                raise AccountingError("试点配置版本已变化，请刷新后重试", 409)
            deps = self._deps(conn, draft["id"])
            blockers = [
                item for item in deps if item["required"] and item["status"] == "unresolved"
            ]
            if blockers:
                raise AccountingError(
                    "仍有未确认的客户资料: " + "、".join(item["label"] for item in blockers), 409
                )
            config = json.loads(draft["config_json"])
            if not config.get("authority_mode"):
                raise AccountingError("必须确认业务权威边界", 409)
            active = self._latest(conn, company_id, "confirmed")
            if active:
                conn.execute(
                    "UPDATE tax_pilot_profiles SET status='superseded' WHERE id=?", (active["id"],)
                )
                conn.execute(
                    "UPDATE tax_pilot_previews SET invalidated_at=? WHERE profile_id=? AND invalidated_at IS NULL",
                    (now(), active["id"]),
                )
            conn.execute(
                "UPDATE tax_pilot_profiles SET status='confirmed',confirmed_by=?,confirmed_at=? WHERE id=?",
                (actor.user_id, now(), draft["id"]),
            )
            conn.execute(
                "INSERT INTO audit_events(firm_id,book_id,user_id,action,object_id,details,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    actor.firm_id,
                    None,
                    actor.user_id,
                    "tax.pilot.confirm",
                    draft["id"],
                    _canonical({"company_id": company_id, "version": int(draft["version"])}),
                    now(),
                ),
            )
            confirmed = conn.execute(
                "SELECT * FROM tax_pilot_profiles WHERE id=?", (draft["id"],)
            ).fetchone()
            return _present_profile(confirmed, deps, [], confirmed)

    def preview(self, actor, company_id, purpose="tax_preview"):
        purpose = str(purpose or "tax_preview")[:80]
        with self.db.transaction(write=True, actor=actor) as conn:
            self._company(conn, actor, company_id)
            active = self._latest(conn, company_id, "confirmed")
            if not active:
                raise AccountingError("试点配置尚未确认，不能生成正式预览", 409)
            if purpose != "tax_preview":
                raise AccountingError("预览用途无效", 422)
            preview_id = uuid4().hex
            conn.execute(
                "INSERT INTO tax_pilot_previews(id,profile_id,purpose,fingerprint,created_by,created_at) VALUES(?,?,?,?,?,?)",
                (preview_id, active["id"], purpose, active["fingerprint"], actor.user_id, now()),
            )
            return {
                "id": preview_id,
                "profile_id": active["id"],
                "profile_version": int(active["version"]),
                "fingerprint": active["fingerprint"],
                "purpose": purpose,
                "created_at": now(),
            }

    def validate_preview(self, actor, company_id, preview_id):
        with self.db.transaction(actor=actor) as conn:
            self._company(conn, actor, company_id)
            preview = conn.execute(
                "SELECT * FROM tax_pilot_previews WHERE id=? AND invalidated_at IS NULL",
                (preview_id,),
            ).fetchone()
            if not preview:
                raise AccountingError("预览已失效，请按当前已确认规则重新生成", 409)
            active = self._latest(conn, company_id, "confirmed")
            if (
                not active
                or active["id"] != preview["profile_id"]
                or active["fingerprint"] != preview["fingerprint"]
            ):
                conn.execute(
                    "UPDATE tax_pilot_previews SET invalidated_at=? WHERE id=?", (now(), preview_id)
                )
                raise AccountingError("规则版本已变化，预览已失效", 409)
            return {
                "valid": True,
                "profile_id": active["id"],
                "profile_version": int(active["version"]),
                "fingerprint": active["fingerprint"],
            }
