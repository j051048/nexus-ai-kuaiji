from fastapi import APIRouter, Depends, Query, Request

from .models import (
    ReportCommand,
    ReportDifferenceNote,
    ReportGenerate,
    ReportNote,
    ReportReconcile,
)
from .profit import ProfitMappingWrite


def build_report_router(actor_dependency):
    router = APIRouter(prefix="/api/reports", tags=["B04报表"])

    def service(request: Request):
        return request.app.state.reports

    def profit(request: Request):
        return request.app.state.profit

    def fleet(request: Request):
        return request.app.state.fleet

    @router.get("/profit/mapping")
    def profit_mapping(
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        profit=Depends(profit),
    ):
        return profit.mapping(current, book_id)

    @router.put("/profit/mapping")
    def save_profit_mapping(
        payload: ProfitMappingWrite,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        profit=Depends(profit),
    ):
        return profit.save_mapping(current, book_id, payload)

    @router.get("/profit/variance")
    def profit_variance(
        book_id: str,
        baseline_book_id: str,
        current=Depends(actor_dependency),
        profit=Depends(profit),
        line_code: str = "",
    ):
        return profit.variance(current, book_id, baseline_book_id, line_code)

    @router.get("/profit")
    def profit_overview(
        book_id: str = Query(min_length=1, max_length=100),
        period: str = Query(min_length=7, max_length=7),
        line_code: str = Query(default="", max_length=40),
        current=Depends(actor_dependency),
        profit=Depends(profit),
    ):
        return profit.overview(current, book_id, period, line_code)

    @router.get("/fleet")
    def fleet_overview(
        book_id: str = Query(min_length=1, max_length=100),
        period: str = Query(min_length=7, max_length=7),
        current=Depends(actor_dependency),
        fleet=Depends(fleet),
    ):
        return fleet.overview(current, book_id, period)

    @router.post("", status_code=201)
    def create(
        payload: ReportGenerate, current=Depends(actor_dependency), reports=Depends(service)
    ):
        return reports.create(current, payload.book_id, payload.period)

    @router.get("")
    def list_reports(
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        reports=Depends(service),
    ):
        return {"reports": reports.list(current, book_id)}

    @router.post("/{report_id}/validate")
    def validate(report_id: str, current=Depends(actor_dependency), reports=Depends(service)):
        return reports.validate(current, report_id)

    @router.post("/{report_id}/review")
    def review(
        report_id: str,
        payload: ReportCommand,
        current=Depends(actor_dependency),
        reports=Depends(service),
    ):
        return reports.review(current, report_id, payload)

    @router.post("/{report_id}/publish")
    def publish(
        report_id: str,
        payload: ReportCommand,
        current=Depends(actor_dependency),
        reports=Depends(service),
    ):
        return reports.publish(current, report_id, payload)

    @router.post("/{report_id}/notes", status_code=201)
    def note(
        report_id: str,
        payload: ReportNote,
        current=Depends(actor_dependency),
        reports=Depends(service),
    ):
        return reports.add_note(current, report_id, payload)

    @router.post("/{report_id}/reconcile")
    def reconcile(
        report_id: str,
        payload: ReportReconcile,
        current=Depends(actor_dependency),
        reports=Depends(service),
    ):
        return reports.reconcile(current, report_id, payload)

    @router.post("/{report_id}/reconciliation-notes", status_code=201)
    def reconciliation_note(
        report_id: str,
        payload: ReportDifferenceNote,
        current=Depends(actor_dependency),
        reports=Depends(service),
    ):
        return reports.explain_difference(current, report_id, payload)

    @router.get("/{report_id}/reconciliation-notes")
    def reconciliation_notes(
        report_id: str,
        reference_sha256: str = Query(pattern=r"^[0-9a-f]{64}$"),
        current=Depends(actor_dependency),
        reports=Depends(service),
    ):
        return reports.reconciliation_notes(current, report_id, reference_sha256)

    return router
