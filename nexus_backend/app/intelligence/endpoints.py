"""Trusted endpoint directory and versioned company AI egress policy."""

import ipaddress
import json
import os
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from pydantic import Field, StrictStr, field_validator

from ..accounting.domain import AccountingError
from ..accounting.models import InputModel
from ..accounting.service import AccountingService, now

Scenario = Literal["ocr", "journal", "chat"]
MAX_PATH = 200
METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
    "fd00:ec2::254",
}


class CapabilityCreate(InputModel):
    scenario: Scenario
    model: StrictStr = Field(min_length=1, max_length=200)
    response_format: Literal["json_schema", "json_object"] = "json_schema"
    supports_image: bool = Field(default=False, strict=True)
    context_limit: int = Field(default=128000, strict=True, ge=1, le=1_000_000)
    output_limit: int = Field(default=4096, strict=True, ge=1, le=1_000_000)
    timeout_seconds: int = Field(default=45, strict=True, ge=1, le=90)
    auth_mode: Literal["bearer", "none"] = "bearer"
    pricing_version: StrictStr = Field(default="", max_length=80)
    data_location: StrictStr = Field(min_length=1, max_length=120)
    reason: StrictStr = Field(min_length=3, max_length=1000)


class EndpointCreate(InputModel):
    protocol: Literal["https", "http"] = "https"
    host: StrictStr = Field(min_length=1, max_length=253)
    port: int = Field(default=443, strict=True, ge=1, le=65535)
    path_prefix: StrictStr = Field(default="", max_length=MAX_PATH)
    service_identity: StrictStr = Field(min_length=1, max_length=120)
    allow_http: bool = Field(default=False, strict=True)
    allow_private: bool = Field(default=False, strict=True)
    capabilities: list[CapabilityCreate] = Field(min_length=1, max_length=3)
    reason: StrictStr = Field(min_length=3, max_length=1000)

    @field_validator("capabilities")
    @classmethod
    def unique_scenarios(cls, value):
        if len({item.scenario for item in value}) != len(value):
            raise ValueError("同一端点不能重复配置场景")
        return value

    @field_validator("path_prefix")
    @classmethod
    def valid_path(cls, value):
        if value and (not value.startswith("/") or ".." in value or "?" in value):
            raise ValueError("端点路径必须是安全绝对前缀")
        return value.rstrip("/")


class CompanyPolicyUpdate(InputModel):
    managed: bool = Field(default=True, strict=True)
    local_only: bool = Field(default=False, strict=True)
    allowed_scenarios: list[Scenario] = Field(min_length=1, max_length=3)
    allowed_data_categories: list[StrictStr] = Field(min_length=1, max_length=30)
    allowed_endpoint_ids: list[StrictStr] = Field(default_factory=list, max_length=50)
    reason: StrictStr = Field(min_length=3, max_length=1000)


def canonical_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise AccountingError("AI 端点 URL 无效", 422) from None
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in {"https", "http"}
    ):
        raise AccountingError("AI 端点必须是无用户信息、查询和片段的 HTTP(S) 基地址", 422)
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    host = parsed.hostname.lower()
    netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))


def validate_endpoint(protocol, host, port, path_prefix, allow_http, allow_private):
    host = host.strip().lower().rstrip(".")
    if not host or "*" in host or "/" in host or " " in host or "\\" in host:
        raise AccountingError("AI 端点主机名无效，不允许通配符或路径", 422)
    if host in METADATA_HOSTS:
        raise AccountingError("AI 端点不得指向云元数据或链路本地目标", 403)
    if protocol == "http" and not allow_http:
        raise AccountingError("AI HTTP 端点必须显式启用 allow_http", 422)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if protocol == "http" and address is not None and not address.is_loopback:
        raise AccountingError("本地 HTTP 例外仅允许回环地址", 422)
    if address is not None:
        if address.is_loopback:
            if not allow_private:
                raise AccountingError("AI 端点指向回环地址时必须显式批准 allow_private", 403)
        elif (
            address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise AccountingError("AI 端点不得指向链路本地、组播、保留或未指定地址", 403)
        elif address.is_private and not allow_private:
            raise AccountingError("AI 端点指向私有地址时必须显式批准 allow_private", 403)
    if not path_prefix.startswith("/") and path_prefix:
        raise AccountingError("AI 端点路径前缀必须以 / 开头", 422)
    return host


def _is_local_endpoint(endpoint):
    host = endpoint["host"].lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


class AIEndpointPolicyService:
    def __init__(self, db):
        self.db = db
        self.accounting = AccountingService(db)

    def _manager(self, conn, actor):
        self.accounting.access.require_manager(conn, actor)

    def create_endpoint(self, actor, request: EndpointCreate):
        host = validate_endpoint(
            request.protocol,
            request.host,
            request.port,
            request.path_prefix,
            request.allow_http,
            request.allow_private,
        )
        with self.db.transaction(write=True, actor=actor) as conn:
            self._manager(conn, actor)
            endpoint_id = uuid4().hex
            try:
                conn.execute(
                    "INSERT INTO ai_trusted_endpoints(id,protocol,host,port,path_prefix,service_identity,"
                    "allow_http,allow_private,active,version,reason,created_by,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,1,1,?,?,?)",
                    (
                        endpoint_id,
                        request.protocol,
                        host,
                        request.port,
                        request.path_prefix,
                        request.service_identity,
                        int(request.allow_http),
                        int(request.allow_private),
                        request.reason,
                        actor.user_id,
                        now(),
                    ),
                )
            except Exception as error:
                if "UNIQUE" in str(error).upper():
                    raise AccountingError("该端点已在可信目录中", 409) from None
                raise
            for capability in request.capabilities:
                conn.execute(
                    "INSERT INTO ai_endpoint_capabilities(endpoint_id,scenario,model,response_format,"
                    "supports_image,context_limit,output_limit,timeout_seconds,auth_mode,pricing_version,"
                    "data_location,active,version,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,1,?,?)",
                    (
                        endpoint_id,
                        capability.scenario,
                        capability.model,
                        capability.response_format,
                        int(capability.supports_image),
                        capability.context_limit,
                        capability.output_limit,
                        capability.timeout_seconds,
                        capability.auth_mode,
                        capability.pricing_version,
                        capability.data_location,
                        capability.reason,
                        now(),
                    ),
                )
            row = conn.execute(
                "SELECT * FROM ai_trusted_endpoints WHERE id=?", (endpoint_id,)
            ).fetchone()
            return self._endpoint_view(conn, row)

    def list_endpoints(self, actor):
        with self.db.transaction(actor=actor) as conn:
            self._manager(conn, actor)
            rows = conn.execute(
                "SELECT * FROM ai_trusted_endpoints ORDER BY service_identity,host,port"
            ).fetchall()
            return [self._endpoint_view(conn, row) for row in rows]

    def get_endpoint(self, actor, endpoint_id):
        with self.db.transaction(actor=actor) as conn:
            self._manager(conn, actor)
            row = conn.execute(
                "SELECT * FROM ai_trusted_endpoints WHERE id=?", (endpoint_id,)
            ).fetchone()
            if not row:
                raise AccountingError("可信端点不存在", 404)
            return self._endpoint_view(conn, row)

    def policy(self, actor, company_id):
        with self.db.transaction(actor=actor) as conn:
            self.accounting.access.company(conn, actor, company_id)
            return self._policy_view(conn, actor, company_id)

    def update_policy(self, actor, company_id, request: CompanyPolicyUpdate):
        with self.db.transaction(write=True, actor=actor) as conn:
            self.accounting.access.company(conn, actor, company_id, manage=True)
            endpoints = []
            for endpoint_id in request.allowed_endpoint_ids:
                row = conn.execute(
                    "SELECT e.*,c.scenario,c.active AS capability_active FROM ai_trusted_endpoints e "
                    "LEFT JOIN ai_endpoint_capabilities c ON c.endpoint_id=e.id "
                    "WHERE e.id=? AND e.active=1",
                    (endpoint_id,),
                ).fetchall()
                if not row:
                    raise AccountingError("策略引用了不存在或停用的可信端点", 409)
                endpoints.extend(row)
                if request.local_only and not _is_local_endpoint(row[0]):
                    raise AccountingError("仅本地策略不能选择非本地端点", 409)
            for endpoint_id in request.allowed_endpoint_ids:
                if not any(
                    row["id"] == endpoint_id
                    and row["capability_active"] == 1
                    and row["scenario"] in request.allowed_scenarios
                    for row in endpoints
                ):
                    raise AccountingError("可信端点未配置所允许的场景能力", 409)
            version = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM ai_company_policies "
                "WHERE firm_id=? AND company_id=?",
                (actor.firm_id, company_id),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO ai_company_policies(firm_id,company_id,version,managed,local_only,"
                "allowed_scenarios_json,allowed_data_categories_json,allowed_endpoint_ids_json,"
                "reason,updated_by,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    actor.firm_id,
                    company_id,
                    version,
                    int(request.managed),
                    int(request.local_only),
                    json.dumps(sorted(set(request.allowed_scenarios))),
                    json.dumps(sorted(set(request.allowed_data_categories)), ensure_ascii=False),
                    json.dumps(sorted(set(request.allowed_endpoint_ids))),
                    request.reason,
                    actor.user_id,
                    now(),
                ),
            )
            return self._policy_view(conn, actor, company_id)

    def _policy_view(self, conn, actor, company_id):
        row = conn.execute(
            "SELECT * FROM ai_company_policies WHERE firm_id=? AND company_id=? "
            "ORDER BY version DESC LIMIT 1",
            (actor.firm_id, company_id),
        ).fetchone()
        if not row:
            return {
                "managed": False,
                "legacy": True,
                "version": 0,
                "local_only": False,
                "allowed_scenarios": [],
                "allowed_data_categories": [],
                "allowed_endpoint_ids": [],
            }
        result = dict(row)
        result["managed"] = bool(result["managed"])
        result["local_only"] = bool(result["local_only"])
        result["allowed_scenarios"] = json.loads(result.pop("allowed_scenarios_json"))
        result["allowed_data_categories"] = json.loads(result.pop("allowed_data_categories_json"))
        result["allowed_endpoint_ids"] = json.loads(result.pop("allowed_endpoint_ids_json"))
        result["legacy"] = False
        return result

    def check_send(
        self, conn, actor, company_id, scenario, data_categories, base_url, model, auth_present
    ):
        """Re-check at send time; returns policy/endpoint evidence, raises on denial."""
        self.accounting.access.company(conn, actor, company_id)
        policy = self._policy_view(conn, actor, company_id)
        if not policy["managed"]:
            production = os.getenv(
                "ACCOUNTING_ENV", "local"
            ).strip().lower() == "production" or any(
                os.getenv(key, "").strip().lower() in {"prod", "production"}
                for key in ("APP_ENV", "ENV", "ENVIRONMENT")
            )
            if production:
                raise AccountingError(
                    "生产环境未配置企业 AI 外发政策，已阻断该调用；请由管理员完成端点与数据授权后再启用",
                    403,
                )
            return {"managed": False, "legacy": True, "policy_version": 0}
        if scenario not in policy["allowed_scenarios"]:
            raise AccountingError("企业未授权该 AI 场景外发", 403)
        if not set(data_categories).issubset(set(policy["allowed_data_categories"])):
            raise AccountingError("请求包含企业未授权外发的数据类别", 403)
        target = canonical_base_url(base_url)
        candidates = []
        for endpoint_id in policy["allowed_endpoint_ids"]:
            endpoint = conn.execute(
                "SELECT e.*,c.scenario,c.model AS capability_model,c.response_format,c.auth_mode,"
                "c.data_location,c.active AS capability_active FROM ai_trusted_endpoints e "
                "JOIN ai_endpoint_capabilities c ON c.endpoint_id=e.id "
                "WHERE e.id=? AND e.active=1 AND c.scenario=? AND c.active=1",
                (endpoint_id, scenario),
            ).fetchone()
            if not endpoint:
                continue
            prefix = endpoint["path_prefix"].rstrip("/")
            host = endpoint["host"]
            netloc = f"[{host}]:{endpoint['port']}" if ":" in host else f"{host}:{endpoint['port']}"
            expected = urlunsplit((endpoint["protocol"], netloc, prefix, "", ""))
            if target == expected or target.startswith(expected + "/"):
                candidates.append(endpoint)
        if not candidates:
            raise AccountingError("当前模型端点不属于企业已批准的可信端点", 403)
        endpoint = max(candidates, key=lambda row: len(row["path_prefix"]))
        if endpoint["capability_model"] and endpoint["capability_model"] != model:
            raise AccountingError("模型标识与可信端点能力配置不一致", 403)
        if policy["local_only"] and not _is_local_endpoint(endpoint):
            raise AccountingError("企业仅允许本地处理，已阻断非本地端点", 403)
        if endpoint["auth_mode"] == "bearer" and not auth_present:
            raise AccountingError("可信端点要求鉴权，但当前未配置密钥", 409)
        if endpoint["auth_mode"] == "none" and auth_present:
            raise AccountingError("无鉴权本地端点不应接收供应商密钥", 409)
        return {
            "managed": True,
            "legacy": False,
            "policy_version": policy["version"],
            "endpoint_id": endpoint["id"],
            "service_identity": endpoint["service_identity"],
            "auth_mode": endpoint["auth_mode"],
            "data_location": endpoint["data_location"],
        }

    @staticmethod
    def _endpoint_view(conn, row):
        result = dict(row)
        result["capabilities"] = [
            dict(item)
            for item in conn.execute(
                "SELECT * FROM ai_endpoint_capabilities WHERE endpoint_id=? ORDER BY scenario",
                (row["id"],),
            ).fetchall()
        ]
        return result
