"""P01: 恢复清单必须覆盖税与交付的真实业务证据。

这些表在恢复后决定“谁在何时把哪个版本给了谁、客户有没有签收、税是怎么算的”。
清单缺一张表，恢复就可能在没有证据的情况下看起来成功。
"""

from __future__ import annotations

import pytest

from scripts import release_recovery_hold as release


REQUIRED = {
    "tax_profiles": ("company_id", "effective_from", "taxpayer_type", "vat_rate_bp"),
    "tax_workpapers": (
        "book_id",
        "period",
        "version",
        "status",
        "profile_id",
        "tax_cents",
        "source_digest",
    ),
    "monthly_delivery_packages": (
        "book_id",
        "period",
        "version",
        "manifest_sha256",
        "contact_user_id",
    ),
    "monthly_delivery_grants": ("book_id", "period", "recipient_user_id", "version", "state"),
    "monthly_delivery_acknowledgements": ("package_id", "manifest_sha256", "created_by"),
    "monthly_delivery_opens": ("package_id", "opened_by", "opened_at"),
}


def test_every_business_evidence_table_is_in_the_release_inventory():
    missing = sorted(set(REQUIRED) - set(release.INVENTORY_TABLES))
    assert missing == [], f"恢复清单缺少：{missing}"


@pytest.mark.parametrize("table,fields", sorted(REQUIRED.items()))
def test_required_fields_are_declared(table, fields):
    declared = release.REQUIRED_COLUMNS[table]
    missing = [name for name in fields if name not in declared]
    assert missing == [], f"{table} 缺少字段：{missing}"
