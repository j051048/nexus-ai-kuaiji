"""The new OCR evidence flow uses actual restricted PostgreSQL/RLS connections."""

import pytest

from test_intelligence_workflow_postgres import (
    ADMIN_URL,
    APP_URL,
    business_case,
    business_postgres,
    pg_ai,
)
from test_paddle_evidence import (
    test_cache_persists_independent_evidence_without_new_charge,
    test_company_cache_isolation_and_revocation,
    test_failed_model_keeps_completed_ocr_metrics_in_database,
    test_intake_preserves_independent_evidence_when_confirmed_into_business,
    test_invalid_ocr_response_releases_llm_reserve_and_allows_intake_retry,
    test_low_confidence_evidence_does_not_enter_reusable_cache,
    test_preview_enforces_current_scope_hash_and_no_store,
    test_quota_reserved_before_ocr_and_actual_llm_tokens_are_charged,
    test_revocation_after_ocr_stops_llm_and_marks_stale,
)

pytestmark = pytest.mark.skipif(
    not ADMIN_URL or not APP_URL, reason="需要隔离 PostgreSQL 管理员及受限运行测试连接"
)


@pytest.fixture
def ai_case(pg_ai):
    return pg_ai
