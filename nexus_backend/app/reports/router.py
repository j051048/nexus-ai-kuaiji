from fastapi import APIRouter, Depends, Query, Request

from .models import ReportCommand, ReportGenerate, ReportNote


def build_report_router(actor_dependency):
    router = APIRouter(prefix="/api/reports", tags=["B04报表"])

    def service(request: Request):
        return request.app.state.reports

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

    return router
