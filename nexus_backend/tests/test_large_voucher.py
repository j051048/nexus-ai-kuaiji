from test_accounting import service, book  # noqa: F401
from app.accounting.models import DraftUpdate, ImportRequest, SourceRow
from app.accounting.seed import LOCAL_ACTOR
from review_policy_fixtures import independent_reviewer


def _money(cents):
    return f"{cents // 100}.{cents % 100:02d}"


def _large(service, book, rows):
    key = f"large-{rows}"
    service.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name=key, rows=[SourceRow(
        external_id=key, date="2026-10-01", description="大凭证", counterparty="合成批次",
        amount="1001.00", direction="expense", evidence_ref="合成证据",
    )]))
    service.suggestions(LOCAL_ACTOR, book)
    draft = service.get_book(LOCAL_ACTOR, book)["drafts"][0]
    base, remainder = divmod(100100, rows)
    debit = [{"account_code": "6602.02", "debit": _money(base + (remainder if i == 0 else 0)), "credit": "0"} for i in range(rows)]
    credit = [{"account_code": "1002", "debit": "0", "credit": _money(base + (remainder if i == 0 else 0))} for i in range(rows)]
    updated = service.update_draft(LOCAL_ACTOR, book, draft["id"], DraftUpdate(
        expected_version=draft["version"], description=f"受控大凭证 {rows}", lines=debit + credit,
    ))
    assert len(updated["lines"]) == rows * 2
    assert updated["issues"] == []
    reviewer = independent_reviewer(service.db, LOCAL_ACTOR, book)
    assert service.approve(reviewer, book, updated["id"], __import__("app.accounting.models", fromlist=["ApprovalRequest"]).ApprovalRequest(expected_version=updated["version"]))["status"] == "approved"


def test_controlled_large_voucher_generation_is_idempotent(service, book):
    key = "large-idempotent"
    service.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name=key, rows=[SourceRow(
        external_id=key, date="2026-10-01", description="大凭证幂等", counterparty="合成批次",
        amount="1001.00", direction="expense", evidence_ref="合成证据",
    )]))
    first = service.suggestions(LOCAL_ACTOR, book)
    assert first["created"] == 1
    drafts_after_first = len(service.get_book(LOCAL_ACTOR, book)["drafts"])
    second = service.suggestions(LOCAL_ACTOR, book)
    assert second["created"] == 0
    assert len(service.get_book(LOCAL_ACTOR, book)["drafts"]) == drafts_after_first == 1


def test_controlled_large_voucher_258_rows(service, book):
    _large(service, book, 258)


def test_controlled_large_voucher_575_rows(service, book):
    _large(service, book, 575)


def test_controlled_large_voucher_1001_rows(service, book):
    _large(service, book, 1001)


def test_untrusted_firm_actor_cannot_read_or_update_large_draft(service, book):
    import pytest
    from app.accounting.domain import Actor
    from app.accounting.service import AccountingError

    _large(service, book, 258)
    draft = service.get_book(LOCAL_ACTOR, book)["drafts"][0]
    foreign = Actor("foreign-j04", "foreign-firm")
    with pytest.raises(AccountingError) as read_error:
        service.get_draft(foreign, book, draft["id"])
    assert read_error.value.status in (403, 404)
    with pytest.raises(AccountingError) as write_error:
        service.update_draft(
            foreign,
            book,
            draft["id"],
            DraftUpdate(expected_version=draft["version"], description="越权改写", lines=draft["lines"]),
        )
    assert write_error.value.status in (403, 404)
    assert service.get_draft(LOCAL_ACTOR, book, draft["id"])["description"] != "越权改写"


def test_book_only_reviewer_revocation_blocks_large_draft(service, book):
    import pytest
    from app.accounting.service import AccountingError

    service.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name="revoke-large", rows=[SourceRow(
        external_id="revoke-large", date="2026-10-01", description="撤权大凭证", counterparty="合成批次",
        amount="100.00", direction="expense", evidence_ref="合成证据",
    )]))
    service.suggestions(LOCAL_ACTOR, book)
    draft = service.get_book(LOCAL_ACTOR, book)["drafts"][0]
    reviewer = independent_reviewer(service.db, LOCAL_ACTOR, book)
    assert service.get_draft(reviewer, book, draft["id"])["id"] == draft["id"]
    with service.db.transaction(write=True, actor=LOCAL_ACTOR) as conn:
        removed = conn.execute(
            "DELETE FROM book_access WHERE user_id=? AND book_id=? AND firm_id=?",
            (reviewer.user_id, book, LOCAL_ACTOR.firm_id),
        ).rowcount
        revoked = conn.execute(
            "UPDATE staff_access_assignments SET state='revoked' WHERE user_id=? AND firm_id=? AND state='active'",
            (reviewer.user_id, LOCAL_ACTOR.firm_id),
        ).rowcount
        assert removed + revoked >= 1
    with pytest.raises(AccountingError) as denied:
        service.get_draft(reviewer, book, draft["id"])
    assert denied.value.status in (403, 404)
    assert service.get_draft(LOCAL_ACTOR, book, draft["id"])["id"] == draft["id"]


def test_revoked_firm_reviewer_cannot_approve_large_draft(service, book):
    from app.accounting.directory import DirectoryService
    from app.accounting.models import ApprovalRequest, BookMemberGrant

    directory = DirectoryService(service.db)
    members = directory.list_firm_members(LOCAL_ACTOR)["members"]
    reviewer = next(
        (row for row in members if row["role"] == "reviewer" and row["user_id"] != LOCAL_ACTOR.user_id),
        None,
    )
    assert reviewer is not None, "seed must provide a non-owner reviewer member"
    directory.grant_book_member(
        LOCAL_ACTOR, book, reviewer["user_id"], BookMemberGrant(role="reviewer")
    )

    service.import_sources(LOCAL_ACTOR, book, ImportRequest(source_name="revoke-large", rows=[SourceRow(
        external_id="revoke-large", date="2026-10-01", description="撤权大凭证",
        counterparty="合成批次", amount="1001.00", direction="expense", evidence_ref="合成证据",
    )]))
    service.suggestions(LOCAL_ACTOR, book)
    draft = service.get_book(LOCAL_ACTOR, book)["drafts"][0]
    updated = service.update_draft(LOCAL_ACTOR, book, draft["id"], DraftUpdate(
        expected_version=draft["version"], description="撤权大凭证",
        lines=[
            {"account_code": "6602.02", "debit": "1001.00", "credit": "0"},
            {"account_code": "1002", "debit": "0", "credit": "1001.00"},
        ],
    ))
    from app.accounting.domain import Actor
    actor = Actor(reviewer["user_id"], LOCAL_ACTOR.firm_id)
    assert service.get_draft(actor, book, updated["id"])["id"] == updated["id"]

    directory.revoke_firm_member(LOCAL_ACTOR, reviewer["user_id"])
    with __import__("pytest").raises(Exception):
        service.approve(actor, book, updated["id"], ApprovalRequest(expected_version=updated["version"]))
