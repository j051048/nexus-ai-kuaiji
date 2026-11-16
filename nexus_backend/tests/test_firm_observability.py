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
    operations = value["operations"]
    assert set(operations) == {
        "materials_pending",
        "drafts_pending",
        "source_rejections",
        "notification_unknown",
        "oldest_queue_age_seconds",
    }
    assert all(isinstance(v, int) and v >= 0 for v in operations.values())
    assert all(
        signal["target"] in {"queue", "operations", "runs", "workbench"}
        and signal["automatic_action"] is False
        for signal in value["signals"]
    )
    codes = {signal["code"] for signal in value["signals"]}
    if operations["materials_pending"]:
        assert "material_backlog" in codes
    if operations["drafts_pending"]:
        assert "review_backlog" in codes
    if operations["source_rejections"]:
        assert "source_rejections" in codes
    if operations["notification_unknown"]:
        assert "notification_unknown" in codes


def test_operations_sla_breaches_are_explicit():
    sla = FirmObservabilityService._sla(
        {
            "materials_pending": 0,
            "drafts_pending": 0,
            "source_rejections": 1,
            "notification_unknown": 2,
            "oldest_queue_age_seconds": 901,
        }
    )
    assert sla["automatic_action"] is False
    assert sla["thresholds"] == {
        "queue_age_seconds": 900,
        "source_rejections": 0,
        "notification_unknown": 0,
    }
    assert {item["code"] for item in sla["breaches"]} == {
        "queue_age",
        "source_rejections",
        "notification_unknown",
    }
    clean = FirmObservabilityService._sla(
        {
            "source_rejections": 0,
            "notification_unknown": 0,
            "oldest_queue_age_seconds": 900,
        }
    )
    assert clean["breaches"] == []
