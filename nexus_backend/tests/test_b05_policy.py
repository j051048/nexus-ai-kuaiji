"""B05 trusted endpoints, company egress policy and bounded local compatibility."""

import json

import httpx
import pytest

from app.accounting.domain import AccountingError
from app.intelligence.endpoints import (
    AIEndpointPolicyService,
    CapabilityCreate,
    CompanyPolicyUpdate,
    EndpointCreate,
    validate_endpoint,
)
from app.intelligence.cache import config_key
from app.intelligence.provider import AISettings, OpenAICompatibleProvider, ProviderError
from test_business import business


VALID_SUGGESTION = {
    "description": "合成记账建议",
    "lines": [
        {
            "account_code": "1002",
            "debit": "1.00",
            "credit": "0.00",
            "auxiliary_values": {},
        }
    ],
    "reasoning": "仅用于传输边界测试",
    "evidence_refs": [],
    "warnings": [],
}


def endpoint_service(case, *, local=False):
    service = AIEndpointPolicyService(case.db)
    if local:
        endpoint = service.create_endpoint(
            case.manager,
            EndpointCreate(
                protocol="http",
                host="127.0.0.1",
                port=18080,
                path_prefix="/v1",
                service_identity="本地测试模型",
                allow_http=True,
                allow_private=True,
                capabilities=[
                    CapabilityCreate(
                        scenario="ocr",
                        model="local-ocr",
                        auth_mode="none",
                        data_location="本机",
                        reason="本地无鉴权测试",
                    )
                ],
                reason="B05 本地端点",
            ),
        )
    else:
        endpoint = service.create_endpoint(
            case.manager,
            EndpointCreate(
                host="ai.example.test",
                port=443,
                path_prefix="/v1",
                service_identity="合成模型服务",
                capabilities=[
                    CapabilityCreate(
                        scenario="ocr",
                        model="vision-1",
                        supports_image=True,
                        data_location="境外测试区域",
                        reason="B05 合成端点",
                    ),
                    CapabilityCreate(
                        scenario="journal",
                        model="journal-1",
                        data_location="境外测试区域",
                        reason="B05 合成端点",
                    ),
                ],
                reason="B05 合成端点",
            ),
        )
    policy = service.update_policy(
        case.manager,
        case.company["id"],
        CompanyPolicyUpdate(
            managed=True,
            local_only=local,
            allowed_scenarios=["ocr", "journal"],
            allowed_data_categories=["document", "counterparty", "amount", "accounting"],
            allowed_endpoint_ids=[endpoint["id"]],
            reason="B05 企业外发政策",
        ),
    )
    return service, endpoint, policy


def test_endpoint_validation_rejects_metadata_and_unapproved_private_targets():
    with pytest.raises(AccountingError, match="元数据"):
        validate_endpoint("https", "169.254.169.254", 443, "", False, False)
    with pytest.raises(AccountingError, match="私有"):
        validate_endpoint("https", "10.0.0.8", 443, "", False, False)
    with pytest.raises(AccountingError, match="回环"):
        validate_endpoint("http", "10.0.0.8", 80, "", True, True)
    with pytest.raises(AccountingError, match="元数据|链路"):
        validate_endpoint("https", "fd00:ec2::254", 443, "", False, True)
    with pytest.raises(AccountingError, match="私有"):
        validate_endpoint("https", "fd00::1", 443, "", False, False)
    assert validate_endpoint("https", "::1", 443, "", False, True) == "::1"
    assert validate_endpoint("http", "127.0.0.1", 18080, "/v1", True, True) == "127.0.0.1"


def test_company_policy_blocks_unapproved_endpoint_scenario_category_and_cloud(business):
    case = business
    service, endpoint, policy = endpoint_service(case)
    assert policy["version"] == 1
    with case.db.transaction() as conn:
        evidence = service.check_send(
            conn,
            case.manager,
            case.company["id"],
            "ocr",
            {"document", "amount"},
            "https://ai.example.test/v1",
            "vision-1",
            True,
        )
        assert evidence["managed"] is True
        assert evidence["endpoint_id"] == endpoint["id"]
    with case.db.transaction() as conn:
        with pytest.raises(AccountingError, match="场景"):
            service.check_send(
                conn,
                case.manager,
                case.company["id"],
                "chat",
                {"chat"},
                "https://ai.example.test/v1",
                "vision-1",
                True,
            )
        with pytest.raises(AccountingError, match="类别"):
            service.check_send(
                conn,
                case.manager,
                case.company["id"],
                "ocr",
                {"payroll"},
                "https://ai.example.test/v1",
                "vision-1",
                True,
            )
        with pytest.raises(AccountingError, match="可信端点"):
            service.check_send(
                conn,
                case.manager,
                case.company["id"],
                "ocr",
                {"document"},
                "https://evil.example.test/v1",
                "vision-1",
                True,
            )
    local_service, local_endpoint, local_policy = endpoint_service(case, local=True)
    assert local_policy["local_only"] is True
    with case.db.transaction() as conn:
        with pytest.raises(AccountingError, match="本地|可信端点"):
            local_service.check_send(
                conn,
                case.manager,
                case.company["id"],
                "ocr",
                {"document"},
                "https://ai.example.test/v1",
                "vision-1",
                True,
            )
        assert local_service.check_send(
            conn,
            case.manager,
            case.company["id"],
            "ocr",
            {"document"},
            "http://127.0.0.1:18080/v1",
            "local-ocr",
            False,
        )["auth_mode"] == "none"
        with pytest.raises(AccountingError, match="密钥"):
            local_service.check_send(
                conn,
                case.manager,
                case.company["id"],
                "ocr",
                {"document"},
                "http://127.0.0.1:18080/v1",
                "local-ocr",
                True,
            )


def test_production_without_managed_policy_blocks_legacy_ai(business, monkeypatch):
    case = business
    monkeypatch.setenv("ACCOUNTING_ENV", "production")
    service = AIEndpointPolicyService(case.db)
    with case.db.transaction() as conn:
        with pytest.raises(AccountingError, match="未配置企业 AI 外发政策"):
            service.check_send(
                conn,
                case.manager,
                case.company["id"],
                "ocr",
                {"document"},
                "https://legacy.example.test/v1",
                "legacy-model",
                True,
            )


def test_provider_accepts_only_one_complete_json_fence_when_enabled():
    def handler(request):
        assert request.headers.get("authorization") == "Bearer synthetic-key"
        body = {"choices": [{"message": {"content": "```json\n" + json.dumps(VALID_SUGGESTION) + "\n```"}, "finish_reason": "stop"}]}
        return httpx.Response(200, json=body)

    settings = AISettings(
        journal_base_url="https://ai.example.test/v1",
        journal_api_key="synthetic-key",
        journal_model="journal-1",
        allow_single_json_fence=True,
    )
    provider = OpenAICompatibleProvider(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    result = provider.suggest_journal({})
    assert result.data.lines[0].account_code == "1002"

    def invalid_handler(request):
        body = {"choices": [{"message": {"content": "```json\n{}\n```\n```json\n{}\n```"}, "finish_reason": "stop"}]}
        return httpx.Response(200, json=body)

    invalid = OpenAICompatibleProvider(
        AISettings(
            journal_base_url="https://ai.example.test/v1",
            journal_api_key="synthetic-key",
            journal_model="journal-1",
            allow_single_json_fence=True,
        ),
        httpx.Client(transport=httpx.MockTransport(invalid_handler)),
    )
    with pytest.raises(ProviderError, match="无效结构化"):
        invalid.suggest_journal({})


def test_cache_key_changes_with_endpoint_policy():
    settings = AISettings(
        ocr_base_url="https://ai.example.test/v1",
        ocr_api_key="synthetic-key",
        ocr_model="vision-1",
    )
    first = config_key(settings, "image/jpeg", policy_key="1:endpoint-a")
    second = config_key(settings, "image/jpeg", policy_key="1:endpoint-b")
    assert first != second


def test_failed_parse_keeps_provider_usage_for_cost():
    def handler(request):
        body = {
            "choices": [
                {
                    "message": {"content": "```json\n{\"documents\": []}\n```"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        }
        return httpx.Response(200, json=body)

    provider = OpenAICompatibleProvider(
        AISettings(
            journal_base_url="https://ai.example.test/v1",
            journal_api_key="synthetic-key",
            journal_model="journal-1",
            allow_single_json_fence=True,
        ),
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(ProviderError) as caught:
        provider.suggest_journal({})
    assert caught.value.usage is not None
    assert caught.value.usage.total_tokens == 10


def test_provider_local_no_auth_does_not_send_authorization_header():
    captured = {}

    def handler(request):
        captured["authorization"] = request.headers.get("authorization")
        body = {"choices": [{"message": {"content": json.dumps(VALID_SUGGESTION)}, "finish_reason": "stop"}]}
        return httpx.Response(200, json=body)

    settings = AISettings(
        journal_base_url="http://127.0.0.1:18080/v1",
        journal_model="local-ocr",
        allow_http=True,
        allow_no_auth=True,
    )
    provider = OpenAICompatibleProvider(settings, httpx.Client(transport=httpx.MockTransport(handler)))
    provider.suggest_journal({})
    assert captured["authorization"] is None
