"""G1: a T+ artifact is not legal evidence until an independent review."""

import json

import pytest

from app.accounting.domain import AccountingError
from app.delivery.external_evidence import MonthlyExternalEvidenceService
from app.delivery.monthly_models import ExternalEvidenceCreate, ExternalEvidenceReview
from app.integrations.models import BridgeConfiguration
from app.integrations.service import FinancialBridgeService
from test_monthly_delivery_package import freeze, ready_case
from test_p0_close_dependencies import close_case  # noqa: F401 - pytest fixture
from test_business import business  # noqa: F401 - pytest fixture


def enable_external(c):
    FinancialBridgeService(c.db).configure(
        c.manager,
        c.book["id"],
        BridgeConfiguration(
            expected_version=0,
            external_book_id="tplus-g1",
            accounts=[
                {"local_code": "1002", "external_code": "100201"},
                {"local_code": "6602.02", "external_code": "660202"},
            ],
            reason="G1 外部账模式核验",
            idempotency_key="g1-bridge-config",
        ),
    )


def evidence_request(c, **changes):
    return ExternalEvidenceCreate(
        **{
            "report_id": c.report["report"]["id"],
            "expected_report_version": c.report["report"]["version"],
            "source_kind": "tplus_report",
            "source_name": "合成T+资产负债表与利润表",
            "source_version": "2026-10-v1",
            "content": json.dumps(
                {"book_id": c.book["id"], "period": "2026-10", "balanced": True},
                ensure_ascii=False,
                sort_keys=True,
            ),
            "note": "客户 T+ 导出报表，登记待独立核验",
            **changes,
        }
    )


def test_external_mode_blocks_package_until_independent_verification(ready_case):
    c = ready_case
    enable_external(c)
    with pytest.raises(AccountingError) as blocked:
        freeze(c)
    assert blocked.value.status == 409
    assert "外部" in str(blocked.value)

    service = MonthlyExternalEvidenceService(c.db)
    record = service.create(c.manager, c.book["id"], evidence_request(c))
    assert record["status"] == "pending" and record["can_verify"] is False
    assert service.list(c.manager, c.book["id"])["external_mode"] is True

    with pytest.raises(AccountingError) as still_blocked:
        freeze(c)
    assert still_blocked.value.status == 409

    with pytest.raises(AccountingError) as self_review:
        service.review(
            c.manager,
            c.book["id"],
            record["id"],
            ExternalEvidenceReview(decision="verified", note="登记人自行核验"),
        )
    assert self_review.value.status == 403

    verified = service.review(
        c.reviewer,
        c.book["id"],
        record["id"],
        ExternalEvidenceReview(decision="verified", note="与客户 T+ 报表期间金额核对一致"),
    )
    assert verified["status"] == "verified" and verified["verified_by"] == c.reviewer.user_id

    package = freeze(c)
    assert package["external_import_status"] == "verified_external_evidence"
    assert package["external_evidence"]["sha256"] == record["sha256"]
    raw = _manifest(c, package)
    assert raw["external_evidence"]["verified_by"] == c.reviewer.user_id


def _manifest(c, package):
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(c.packages.download(c.manager, c.book["id"], package["id"]))) as z:
        return json.loads(z.read("manifest.json"))


def test_external_evidence_rejects_wrong_report_and_reserved_content(ready_case):
    c = ready_case
    service = MonthlyExternalEvidenceService(c.db)
    with pytest.raises(AccountingError) as wrong:
        service.create(
            c.manager,
            c.book["id"],
            evidence_request(c, expected_report_version=c.report["report"]["version"] + 1),
        )
    assert wrong.value.status == 409
    first = service.create(c.manager, c.book["id"], evidence_request(c))
    same = service.create(c.manager, c.book["id"], evidence_request(c))
    assert same["id"] == first["id"]
    with pytest.raises(AccountingError) as conflict:
        service.create(c.manager, c.book["id"], evidence_request(c, note="不同备注不应重建同一原件"))
    assert conflict.value.status == 409

    rejected = service.review(
        c.reviewer,
        c.book["id"],
        first["id"],
        ExternalEvidenceReview(decision="rejected", note="缺少 T+ 报表签章页"),
    )
    assert rejected["status"] == "rejected"
    with pytest.raises(AccountingError) as frozen:
        service.review(
            c.reviewer,
            c.book["id"],
            first["id"],
            ExternalEvidenceReview(decision="verified", note="事后翻案"),
        )
    assert frozen.value.status == 409
