from __future__ import annotations

import pytest

from app.accounting.admin_service import AdminService
from app.accounting.domain import AccountingError
from app.accounting.models import CompanyCreate
from app.security.redaction import (
    mask_bank_account,
    mask_id_card,
    mask_phone,
    mask_tax_id,
    redact_sensitive,
)
from test_admin_workspace import company_admin
from test_directory import case


def test_masking_keeps_only_needed_display_suffixes():
    assert mask_bank_account("6222 0000 1111 1234") == "****1234"
    assert mask_tax_id("913300001234567890") == "9133**********7890"
    assert mask_phone("+86 138 0013 8000") == "138****8000"
    assert mask_id_card("110101199001011234") == "110101********1234"
    assert redact_sensitive(
        {"bank_account": "6222000011111234", "nested": [{"mobile": "13800138000"}]}
    ) == {"bank_account": "****1234", "nested": [{"mobile": "138****8000"}]}


def test_admin_tax_id_is_masked_and_manager_reveal_is_audited(case):
    created = case.directory.create_company(
        case.actor,
        CompanyCreate(name="掩码测试企业", tax_id="913300001234567890"),
    )
    service = AdminService(case.db)
    listed = service.list_companies(case.actor, search="掩码测试")["companies"]
    assert listed[0]["tax_id"] == "9133**********7890"
    detail = service.get_company(case.actor, created["id"])["company"]
    assert detail["tax_id"] == "9133**********7890"

    revealed = service.reveal_sensitive(case.actor, created["id"])
    assert revealed["tax_id"] == "913300001234567890"
    with case.db.transaction() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM audit_events WHERE action='security.sensitive.reveal' AND object_id=?",
            (created["id"],),
        ).fetchone()[0]
    assert count == 1


def test_company_admin_cannot_reveal_full_tax_id(case):
    created = case.directory.create_company(
        case.actor,
        CompanyCreate(name="越权掩码企业", tax_id="913300001234567891"),
    )
    actor = company_admin(case)
    with pytest.raises(AccountingError, match="事务所负责人"):
        AdminService(case.db).reveal_sensitive(actor, created["id"])
