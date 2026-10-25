from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from typing import Literal

from .models import ManualVoucher, OpeningBind, PostDraft, ReverseVoucher
from .presentation import export_formal_book


def build_posting_router(actor_dependency):
    router = APIRouter(prefix="/api", tags=["B03正式记账"])

    def service(request: Request):
        return request.app.state.posting

    @router.post("/posting/opening-bindings", status_code=201)
    def bind_opening(
        payload: OpeningBind, current=Depends(actor_dependency), posting=Depends(service)
    ):
        return posting.bind_opening(current, payload)

    @router.post("/books/{book_id}/drafts/{draft_id}/post", status_code=201)
    def post_draft(
        book_id: str,
        draft_id: str,
        payload: PostDraft,
        current=Depends(actor_dependency),
        posting=Depends(service),
    ):
        return posting.post_draft(current, book_id, draft_id, payload)

    @router.post("/posting/manual", status_code=201)
    def post_manual(
        payload: ManualVoucher, current=Depends(actor_dependency), posting=Depends(service)
    ):
        return posting.post_manual(current, payload)

    @router.post("/posting/vouchers/{voucher_id}/reverse", status_code=201)
    def reverse(
        voucher_id: str,
        payload: ReverseVoucher,
        current=Depends(actor_dependency),
        posting=Depends(service),
    ):
        return posting.reverse_voucher(current, voucher_id, payload)

    @router.get("/books/{book_id}/vouchers")
    def vouchers(
        book_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0, le=1_000_000),
        current=Depends(actor_dependency),
        posting=Depends(service),
    ):
        return {"vouchers": posting.list_vouchers(current, book_id, limit, offset)}

    @router.get("/posting/vouchers/{voucher_id}")
    def voucher(voucher_id: str, current=Depends(actor_dependency), posting=Depends(service)):
        return posting.voucher_detail(current, voucher_id)

    @router.get("/books/{book_id}/posting-commands/{command_key}")
    def command(
        book_id: str,
        command_key: str,
        current=Depends(actor_dependency),
        posting=Depends(service),
    ):
        return posting.lookup_command(current, book_id, command_key)

    @router.get("/books/{book_id}/trial-balance")
    def trial_balance(
        book_id: str,
        as_of: str | None = Query(default=None, max_length=10),
        current=Depends(actor_dependency),
        posting=Depends(service),
    ):
        return posting.trial_balance(current, book_id, as_of)

    @router.get("/books/{book_id}/ledger/{account_id}")
    def account_ledger(
        book_id: str,
        account_id: str,
        as_of: str | None = Query(default=None, max_length=10),
        current=Depends(actor_dependency),
        posting=Depends(service),
    ):
        return posting.account_ledger(current, book_id, account_id, as_of)

    @router.get("/books/{book_id}/daily-journal")
    def daily_journal(book_id: str, current=Depends(actor_dependency), posting=Depends(service)):
        with posting.db.transaction(actor=current) as conn:
            book, _ = posting._book(conn, current, book_id)
            return {
                "scope": posting._read_scope(book),
                "entries": posting.daily_journal_in_transaction(conn, current, book_id),
            }

    @router.get("/books/{book_id}/aux-ledger")
    def aux_ledger(
        book_id: str,
        key: str = Query(min_length=1, max_length=40),
        value: str = Query(min_length=1, max_length=200),
        current=Depends(actor_dependency),
        posting=Depends(service),
    ):
        return posting.aux_ledger(current, book_id, {key: value})

    @router.get("/books/{book_id}/formal-books/export")
    def export_book(
        book_id: str,
        kind: Literal["trial_balance", "account_ledger", "daily_journal", "aux_ledger"],
        account_id: str | None = Query(default=None, max_length=100),
        key: str | None = Query(default=None, max_length=40),
        value: str | None = Query(default=None, max_length=200),
        as_of: str | None = Query(default=None, max_length=10),
        current=Depends(actor_dependency),
        posting=Depends(service),
    ):
        content = export_formal_book(
            posting,
            current,
            book_id,
            kind,
            account_id=account_id,
            key=key,
            value=value,
            as_of=as_of,
        )
        return Response(
            content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="formal-{kind}.csv"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
