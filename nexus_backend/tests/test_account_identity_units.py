"""Account affiliation is membership, never the list of serviced clients."""

from app.accounting.domain import Actor
from app.accounting.models import CompanyCreate, CompanyMemberGrant, FirmCreate, FirmMemberGrant
from test_directory import case  # noqa: F401


def test_identity_units_are_own_live_memberships_in_selected_firm(case):
    c = case
    user = c.user("unit-employee")
    c.directory.grant_company_member(
        c.actor, c.company["id"], CompanyMemberGrant(email=user.email, role="employee")
    )
    second = c.directory.create_company(c.actor, CompanyCreate(name="同所第二企业"))
    c.directory.grant_company_member(
        c.actor, second["id"], CompanyMemberGrant(email=user.email, role="finance")
    )
    other_owner = c.user("other-unit-owner")
    firm = c.directory.create_firm(other_owner.user_id, FirmCreate(name="其他事务所"))
    actor = Actor(other_owner.user_id, firm["id"])
    other = c.directory.create_company(actor, CompanyCreate(name="其他所企业"))
    c.directory.grant_company_member(
        actor, other["id"], CompanyMemberGrant(email=user.email, role="employee")
    )
    memberships = c.directory.me(user.user_id, c.actor.firm_id)["company_memberships"]
    assert {item["id"]: item["role"] for item in memberships} == {
        c.company["id"]: "employee",
        second["id"]: "finance",
    }
    assert all(set(item) == {"id", "name", "role"} for item in memberships)
    assert c.directory.me(user.user_id, firm["id"])["company_memberships"] == [
        {"id": other["id"], "name": other["name"], "role": "employee"}
    ]
    c.directory.revoke_company_member(c.actor, second["id"], user.user_id)
    assert [
        item["id"] for item in c.directory.me(user.user_id, c.actor.firm_id)["company_memberships"]
    ] == [c.company["id"]]
    # Firm ownership grants access to clients, not employment in every client.
    assert c.directory.me(c.actor.user_id, c.actor.firm_id)["company_memberships"] == []


def test_identity_units_do_not_include_another_member_or_inactive_client(case):
    c = case
    user, peer = c.user("unit-reader"), c.user("unit-peer")
    c.directory.grant_company_member(
        c.actor, c.company["id"], CompanyMemberGrant(email=user.email, role="employee")
    )
    second = c.directory.create_company(c.actor, CompanyCreate(name="同事所属企业"))
    c.directory.grant_company_member(
        c.actor, second["id"], CompanyMemberGrant(email=peer.email, role="finance")
    )
    assert [item["id"] for item in c.directory.me(user.user_id)["company_memberships"]] == [
        c.company["id"]
    ]
    assert c.directory.me(c.user("unit-outsider").user_id)["company_memberships"] == []
    c.directory.grant_firm_member(c.actor, FirmMemberGrant(email=user.email, role="viewer"))
    with c.db.transaction(write=True, actor=c.actor) as conn:
        conn.execute(
            "UPDATE firm_clients SET status='suspended' WHERE firm_id=? AND company_id=?",
            (c.actor.firm_id, c.company["id"]),
        )
    assert c.directory.me(user.user_id, c.actor.firm_id)["company_memberships"] == []
