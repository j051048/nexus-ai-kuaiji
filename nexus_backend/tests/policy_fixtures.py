"""Independent policy approver fixture.

The product requires the policy maker and approver to be different managers.
Tests that only seed one manager use this helper to create a second, real firm
admin through the normal directory service.
"""

from types import SimpleNamespace
from uuid import uuid4

from app.accounting.directory import DirectoryService
from app.accounting.domain import Actor
from app.accounting.models import FirmMemberGrant


def independent_manager(db, actor):
    identity = SimpleNamespace(
        user_id=f"policy-approver-{uuid4().hex[:10]}",
        display_name="独立政策批准人",
        email=f"policy-approver-{uuid4().hex[:10]}@example.test",
    )
    directory = DirectoryService(db)
    directory.register_identity(identity)
    directory.grant_firm_member(
        actor, FirmMemberGrant(email=identity.email, role="admin")
    )
    return Actor(identity.user_id, actor.firm_id)


def approve_policy_independently(opening, db, actor, policy_id):
    approver = independent_manager(db, actor)
    return approver, opening.approve_policy(approver, policy_id)
