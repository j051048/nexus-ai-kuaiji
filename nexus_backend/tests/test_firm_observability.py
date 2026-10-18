from app.observability.service import FirmObservabilityService
from test_business import business


def test_firm_observability_returns_queue_metrics_and_projection(business):
    c = business
    service = FirmObservabilityService(c.db, c.service)
    value = service.snapshot(c.manager)
    assert "ocr" in value["queues"]
    assert "batch" in value["queues"]
    assert value["projection"]["total"] >= 2
    assert value["metrics"]["transactions"] >= 0
    assert value["ai_cost_cny"] >= 0
