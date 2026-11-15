"""TAX01 versioned pilot boundary on real restricted PostgreSQL."""

import pytest

from app.accounting.domain import AccountingError
from app.tax.pilot import DEPENDENCIES, PilotBoundaryService
from test_tax_workpaper_postgres import case, pg  # noqa: F401


def deps(status="unresolved", evidence=""):
    return [
        {"code": code, "status": status, "evidence_ref": evidence, "owner_role": "客户会计"}
        for code, _label in DEPENDENCIES
    ]


def test_pilot_boundary_confirm_preview_and_revocation(case):
    service = PilotBoundaryService(case.db)
    company = case.book["company_id"]
    ready = service.readiness(case.maker, company)
    assert ready["exists"] is False

    draft = service.update(case.maker, company, 0, {"authority_mode": "internal"}, deps())
    assert draft["version"] == 1
    with pytest.raises(AccountingError) as error:
        service.confirm(case.maker, company, 1)
    assert error.value.status == 409

    second = service.update(
        case.maker, company, 1, {"authority_mode": "internal"}, deps("confirmed", "客户确认 2026-11-10")
    )
    confirmed = service.confirm(case.maker, company, second["version"])
    assert confirmed["status"] == "confirmed"
    preview = service.preview(case.maker, company)
    assert service.validate_preview(case.maker, company, preview["id"])["valid"] is True

    # A non-manager cannot change the boundary even though the book is visible.
    with pytest.raises(AccountingError) as error:
        service.update(case.viewer, company, 2, {"authority_mode": "internal"}, deps())
    assert error.value.status in {403, 404}

    # A new draft invalidates the existing preview immediately.
    service.update(case.maker, company, 2, {"authority_mode": "internal"}, deps())
    with pytest.raises(AccountingError) as error:
        service.validate_preview(case.maker, company, preview["id"])
    assert error.value.status == 409
