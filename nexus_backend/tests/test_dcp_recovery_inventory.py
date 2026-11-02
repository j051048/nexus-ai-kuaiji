def test_manual_investigation_is_content_bound_in_release_inventory():
    from scripts.release_recovery_hold import INVENTORY_TABLES, REQUIRED_COLUMNS

    assert "notification_investigations" in INVENTORY_TABLES
    assert set(REQUIRED_COLUMNS["notification_investigations"]) >= {
        "notification_id",
        "firm_id",
        "company_id",
        "book_id",
        "period",
        "package_id",
        "package_version",
        "manifest_sha256",
        "recipient_user_id",
        "attempts",
        "revision",
        "conclusion",
        "evidence_ref",
        "reason",
        "created_by",
        "created_at",
        "idempotency_key",
    }


def test_manual_evidence_is_hashed_in_recovery_report():
    import json
    from scripts.release_recovery_hold import REQUIRED_COLUMNS, pending_inventory
    from test_n03_recovery import InventoryConnection

    connection = InventoryConnection()
    row = dict.fromkeys(REQUIRED_COLUMNS["notification_investigations"], "synthetic")
    row.update(
        evidence_ref="private synthetic contact evidence",
        reason="private synthetic investigation note",
    )
    connection.tables["notification_investigations"] = [row]
    before = pending_inventory(connection)
    assert "private synthetic" not in json.dumps(before)
    row["evidence_ref"] += " changed"
    assert before["sha256"] != pending_inventory(connection)["sha256"]
