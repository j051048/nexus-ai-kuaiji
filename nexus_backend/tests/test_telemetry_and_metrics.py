"""Prometheus exposition: format correctness, cardinality and the access gate."""

import pytest

from test_business_api import auth, client  # noqa: F401

from app import main as main_module
from app.observability import metrics


@pytest.fixture(autouse=True)
def clean_registry():
    metrics.reset()
    yield
    metrics.reset()


def _text(client) -> str:
    response = client.get("/metrics")  # TestClient connects over loopback
    assert response.status_code == 200
    return response.text


def test_render_emits_help_type_and_values():
    metrics.increment("accounting_vouchers_posted_total", firm="firm-1")
    metrics.increment("accounting_vouchers_posted_total", firm="firm-1")
    metrics.describe("accounting_vouchers_posted_total", "counter", "Vouchers posted")
    text = metrics.render()
    assert "# TYPE accounting_vouchers_posted_total counter" in text
    assert 'accounting_vouchers_posted_total{firm="firm-1"} 2' in text


def test_histogram_buckets_are_cumulative_and_end_at_inf():
    for value in (0.003, 0.02, 0.4, 3.0):
        metrics.observe("http_request_duration_seconds", value, method="GET")
    text = metrics.render()
    assert 'http_request_duration_seconds_bucket{le="0.005",method="GET"} 1' in text
    assert 'http_request_duration_seconds_bucket{le="0.05",method="GET"} 2' in text
    assert 'http_request_duration_seconds_bucket{le="+Inf",method="GET"} 4' in text
    # Bucket edges are emitted with integer formatting when exact: le="1", not "1.0".
    assert 'http_request_duration_seconds_bucket{le="1",method="GET"} 3' in text
    assert 'http_request_duration_seconds_count{method="GET"} 4' in text
    assert 'http_request_duration_seconds_sum{method="GET"} 3.423' in text


def test_label_values_are_escaped_not_injected():
    metrics.increment("probe_total", route='/api/x"y\nz\\')
    line = [ln for ln in metrics.render().splitlines() if ln.startswith("probe_total{")][0]
    assert '\\"y\\nz\\\\' in line
    assert line.count("\n") == 0


def test_metrics_count_real_requests_and_use_route_templates(client):
    for _ in range(3):
        client.get("/api/health")
    text = _text(client)
    assert 'http_requests_total{method="GET",route="/api/health",status="200"} 3' in text


def test_route_labels_are_templates_so_book_ids_do_not_explode_cardinality(client):
    client.get("/api/books/book-a")
    client.get("/api/books/book-b")
    text = _text(client)
    assert "book-a" not in text and "book-b" not in text


def test_require_token_makes_the_shared_secret_mandatory(client, monkeypatch):
    monkeypatch.setattr(main_module, "is_loopback", lambda host: True)
    monkeypatch.setenv("ACCOUNTING_METRICS_TOKEN", "s3cret")
    monkeypatch.setenv("ACCOUNTING_METRICS_REQUIRE_TOKEN", "1")
    assert client.get("/metrics").status_code == 403
    assert client.get("/metrics", headers={"x-metrics-token": "nope"}).status_code == 403
    ok = client.get("/metrics", headers={"x-metrics-token": "s3cret"})
    assert ok.status_code == 200
    assert ok.headers["cache-control"] == "no-store"


def test_loopback_scrapes_work_without_a_token(client, monkeypatch):
    monkeypatch.delenv("ACCOUNTING_METRICS_TOKEN", raising=False)
    monkeypatch.delenv("ACCOUNTING_METRICS_REQUIRE_TOKEN", raising=False)
    monkeypatch.setattr(main_module, "is_loopback", lambda host: True)
    assert client.get("/metrics").status_code == 200


def test_metrics_payload_is_plain_text_not_json(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def test_business_helpers_emit_the_sla_counters():
    metrics.voucher_posted()
    metrics.voucher_posted()
    metrics.tax_workpaper_generated()
    metrics.payment_state("approved")
    text = metrics.render()
    assert "accounting_vouchers_posted_total 2" in text
    assert "tax_workpapers_generated_total 1" in text
    assert 'payment_orders_state_total{state="approved"} 1' in text
    assert "# TYPE accounting_vouchers_posted_total counter" in text
