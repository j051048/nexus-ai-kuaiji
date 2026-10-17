from app.cockpit.service import FirmCockpitService
from test_business import business


def test_firm_cockpit_aggregates_stage_workload_and_risk(business):
    c = business
    cockpit = FirmCockpitService(c.db, c.service)
    result = cockpit.snapshot(c.manager)
    assert result["summary"]["clients"] >= 2
    assert result["summary"]["opened"] >= 2
    assert result["summary"]["projection_stale"] >= 0
    assert {row["company_id"] for row in result["companies"]} >= {
        c.company["id"],
        c.other["id"],
    }
    assert result["workload"]
    assert all("risk" in row for row in result["companies"])
    assert result["risks"][0]["risk"] >= result["risks"][-1]["risk"]
