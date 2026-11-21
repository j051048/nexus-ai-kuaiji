"""PLT06-B: revoke takes effect on the next request; self-review stays refused.

PLT06-A (summary / detail / origin as separately grantable levels) is not
expressible in the current model: `book_access` offers editor|reviewer and any
read grant also unlocks attachments. This file proves the reachable half and
records the missing separation instead of faking a summary-only identity.
"""

import pytest

from app.accounting.models import ApprovalRequest, ImportRequest
from app.accounting.directory import DirectoryService
from app.accounting.seed import LOCAL_ACTOR
from app.accounting.service import AccountingError
from test_accounting import add_actor, book, row, service  # noqa: F401 - fixtures


def _draft(service, book_id, actor):
    service.import_sources(actor, book_id, ImportRequest(source_name="PLT06", rows=[row()]))
    service.suggestions(actor, book_id)
    drafts = service.get_book(actor, book_id)["drafts"]
    assert drafts, "PLT06 fixture must produce one draft"
    return drafts[0]


def test_revoke_book_access_blocks_the_next_request(service, book):  # noqa: F811
    actor = add_actor(service, "plt06-operator", LOCAL_ACTOR.firm_id, "operator", book)
    _draft(service, book, actor)
    DirectoryService(service.db).revoke_book_member(LOCAL_ACTOR, book, actor.user_id)
    with pytest.raises(AccountingError) as refused:
        service.get_book(actor, book)
    assert refused.value.status in (403, 404)


def test_revoked_reviewer_cannot_approve_and_writes_nothing(service, book):  # noqa: F811
    maker = add_actor(service, "plt06-maker", LOCAL_ACTOR.firm_id, "operator", book)
    reviewer = add_actor(service, "plt06-reviewer", LOCAL_ACTOR.firm_id, "reviewer", book)
    draft = _draft(service, book, maker)
    DirectoryService(service.db).revoke_book_member(LOCAL_ACTOR, book, reviewer.user_id)
    with pytest.raises(AccountingError):
        service.approve(
            reviewer, book, draft["id"], ApprovalRequest(expected_version=draft["version"])
        )
    assert service.get_book(maker, book)["drafts"][0]["status"] == "draft"


def test_maker_cannot_approve_own_draft(service, book):  # noqa: F811
    maker = add_actor(service, "plt06-self", LOCAL_ACTOR.firm_id, "reviewer", book)
    draft = _draft(service, book, maker)
    with pytest.raises(AccountingError) as refused:
        service.approve(
            maker, book, draft["id"], ApprovalRequest(expected_version=draft["version"])
        )
    assert "制单责任人不能独立复核本人待审版本" in str(refused.value)
