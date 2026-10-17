from app.accounting.admin_service import AdminService
from app.accounting.catalog import CatalogService
from app.onboarding.models import StartOnboarding
from app.onboarding.service import OnboardingService
from test_business import business


def test_onboarding_run_is_resumable_and_rechecks_readiness(business):
    c = business
    service = OnboardingService(c.db, AdminService(c.db), CatalogService(c.db))
    preview = service.preview(c.manager, c.company["id"], c.book["period"])
    assert preview["company_id"] == c.company["id"]
    assert preview["target_book_id"] == c.book["id"]

    run = service.start(
        c.manager,
        StartOnboarding(
            company_id=c.company["id"],
            period=c.book["period"],
            reason="标准化开通回归",
        ),
    )
    assert run["company_id"] == c.company["id"]
    assert run["status"] in {"ready", "blocked", "completed"}
    assert service.get(c.manager, run["id"])["steps"] == run["steps"]
    refreshed = service.refresh(c.manager, run["id"])
    assert refreshed["steps"]["period"] == c.book["period"]
    listed = service.list(c.manager)
    assert listed["total"] >= 1
