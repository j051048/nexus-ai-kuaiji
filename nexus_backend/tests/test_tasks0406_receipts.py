from test_next06_provider_receipts import (  # noqa: F401
    receipts,
    case,
    ready_case,
    close_case,
    business,
    envelope,
    send,
)


def test_receipt_projection_binds_scope_and_separates_provider_from_customer(receipts):
    c, service, notification = receipts
    send(receipts, envelope(notification, "provider-open", "viewed"))
    send(receipts, envelope(notification, "provider-deliver", "delivered"))
    send(receipts, envelope(notification, "provider-bounce", "bounced"))
    result = service.timeline(c.reviewer, c.book["id"], notification)
    assert result["book_id"] == c.book["id"]
    assert result["company_id"]
    assert result["package_id"] and result["manifest_sha256"]
    assert result["viewed"] and not result["customer_opened"] and not result["signed"]
    assert result["receipt_conflict"] is True
    assert result["can_retry"] is False
