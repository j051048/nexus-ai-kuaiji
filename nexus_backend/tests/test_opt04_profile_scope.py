"""OPT04-B: profile version streams are scoped by firm, not only company."""

from app.accounting.service import Actor
from app.accounting.database import Database
from app.fsc.profiles import REQUIRED, FscProfileService
from app.main import create_app
from fastapi.testclient import TestClient


def _payload():
    return {key: None for key in REQUIRED}


def test_profile_drafts_and_versions_are_isolated_per_firm(tmp_path):
    path = tmp_path / "profile-firm-scope.db"
    app = create_app(path, seed=True, attachment_root=tmp_path / "originals")
    with TestClient(app):
        pass
    db = Database(path)
    with db.connect() as conn:
        firm_a = conn.execute("SELECT id FROM firms ORDER BY id LIMIT 1").fetchone()[0]
        owner_a = conn.execute(
            "SELECT user_id FROM memberships WHERE firm_id=? AND role='owner' LIMIT 1", (firm_a,)
        ).fetchone()[0]
        company = conn.execute("SELECT id FROM companies LIMIT 1").fetchone()[0]
        firm_b, owner_b = "opt04-firm-b", "opt04-owner-b"
        conn.execute(
            "INSERT INTO users(id,name,email) VALUES(?,?,?)",
            (owner_b, "乙所负责人", "opt04-b@example.test"),
        )
        conn.execute(
            "INSERT INTO firms(id,name,created_by) VALUES(?,?,?)", (firm_b, "OPT04乙所", owner_b)
        )
        conn.execute(
            "INSERT INTO memberships(firm_id,user_id,role) VALUES(? ,?,'owner')", (firm_b, owner_b)
        )
        conn.execute(
            "INSERT INTO firm_clients(firm_id,company_id,status) VALUES(?,?,'active')",
            (firm_b, company),
        )
    actor_a = Actor(user_id=owner_a, firm_id=firm_a)
    actor_b = Actor(user_id=owner_b, firm_id=firm_b)
    service = FscProfileService(db)

    a1 = service.create_draft(
        actor_a, company, type("P", (), {"industry": "taxi", "payload": _payload()})()
    )
    b1 = service.create_draft(
        actor_b, company, type("P", (), {"industry": "taxi", "payload": _payload()})()
    )

    assert b1["version"] == 1
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id,status,version FROM fsc_company_profiles WHERE company_id=? ORDER BY firm_id,version",
            (company,),
        ).fetchall()
    assert {(row["id"], row["version"], row["status"]) for row in rows} == {
        (a1["id"], 1, "draft"),
        (b1["id"], 1, "draft"),
    }

    a2 = service.create_draft(
        actor_a, company, type("P", (), {"industry": "taxi", "payload": _payload()})()
    )
    assert a2["version"] == 2
    with db.connect() as conn:
        a1_status = conn.execute(
            "SELECT status FROM fsc_company_profiles WHERE id=?", (a1["id"],)
        ).fetchone()[0]
        b1_status = conn.execute(
            "SELECT status FROM fsc_company_profiles WHERE id=?", (b1["id"],)
        ).fetchone()[0]
    assert a1_status == "superseded"
    assert b1_status == "draft"
    matrix_a = {item["company_id"]: item for item in service.matrix(actor_a)["items"]}[company]
    matrix_b = {item["company_id"]: item for item in service.matrix(actor_b)["items"]}[company]
    assert (matrix_a["version"], matrix_a["status"]) == (2, "draft")
    assert (matrix_b["version"], matrix_b["status"]) == (1, "draft")


def _two_firm_company(tmp_path):
    path = tmp_path / "profile-firm-scope.db"
    app = create_app(path, seed=True, attachment_root=tmp_path / "originals")
    with TestClient(app):
        pass
    db = Database(path)
    with db.connect() as conn:
        firm_a = conn.execute("SELECT id FROM firms ORDER BY id LIMIT 1").fetchone()[0]
        owner_a = conn.execute(
            "SELECT user_id FROM memberships WHERE firm_id=? AND role='owner' LIMIT 1", (firm_a,)
        ).fetchone()[0]
        company = conn.execute("SELECT id FROM companies LIMIT 1").fetchone()[0]
        firm_b, owner_b = "opt04-firm-b", "opt04-owner-b"
        conn.execute(
            "INSERT INTO users(id,name,email) VALUES(?,?,?)",
            (owner_b, "乙所负责人", "opt04-b@example.test"),
        )
        conn.execute(
            "INSERT INTO firms(id,name,created_by) VALUES(?,?,?)", (firm_b, "OPT04乙所", owner_b)
        )
        conn.execute(
            "INSERT INTO memberships(firm_id,user_id,role) VALUES(? ,?,'owner')", (firm_b, owner_b)
        )
        conn.execute(
            "INSERT INTO firm_clients(firm_id,company_id,status) VALUES(?,?,'active')",
            (firm_b, company),
        )
    return (
        db,
        company,
        Actor(user_id=owner_a, firm_id=firm_a),
        Actor(user_id=owner_b, firm_id=firm_b),
    )


def test_contract_and_evidence_versions_are_isolated_per_firm(tmp_path):
    from app.fsc.contracts import ContractWrite, FscContractService
    from app.fsc.evidence import EvidencePolicyWrite, FscEvidenceService, Requirement

    db, company, actor_a, actor_b = _two_firm_company(tmp_path)
    contracts = FscContractService(db)
    write = ContractWrite(
        industry_code="taxi",
        processor_code="taxi_fee",
        capabilities=["taxi_fee_import"],
        source_contracts=[{"code": "xlsx", "fields": ["amount"]}],
    )
    a1 = contracts.create_draft(actor_a, company, write)
    b1 = contracts.create_draft(actor_b, company, write)
    assert (a1["version"], b1["version"]) == (1, 1)
    a2 = contracts.create_draft(actor_a, company, write)
    assert a2["version"] == 2
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT firm_id,version,status FROM fsc_industry_contracts WHERE company_id=? ORDER BY firm_id,version",
            (company,),
        ).fetchall()
    assert {(row["firm_id"], row["version"], row["status"]) for row in rows} == {
        (actor_a.firm_id, 1, "superseded"),
        (actor_a.firm_id, 2, "draft"),
        (actor_b.firm_id, 1, "draft"),
    }

    evidence = FscEvidenceService(db)
    policy = EvidencePolicyWrite(
        industry_code="taxi",
        requirements=[Requirement(action="receive", evidence_kind="invoice")],
    )
    ea1 = evidence.create_draft(actor_a, company, policy)
    eb1 = evidence.create_draft(actor_b, company, policy)
    assert (ea1["version"], eb1["version"]) == (1, 1)
    ea2 = evidence.create_draft(actor_a, company, policy)
    assert ea2["version"] == 2
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT firm_id,version,status FROM fsc_evidence_policies WHERE company_id=? ORDER BY firm_id,version",
            (company,),
        ).fetchall()
    assert {(row["firm_id"], row["version"], row["status"]) for row in rows} == {
        (actor_a.firm_id, 1, "superseded"),
        (actor_a.firm_id, 2, "draft"),
        (actor_b.firm_id, 1, "draft"),
    }
