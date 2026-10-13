from fastapi import APIRouter, Depends, Query, Request

from .models import RunCreate, StandardConfirm, StandardCreate, BenchmarkCreate, BenchmarkCompare
from .benchmarks import BenchmarkService


def build_quality_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/quality", tags=["真实业务评测"])

    def service(request: Request):
        return request.app.state.quality

    @router.get("/benchmarks")
    def benchmarks(
        book_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
        quality=Depends(service),
    ):
        return BenchmarkService(quality).list(actor, book_id, limit, offset)

    @router.post("/benchmarks", status_code=201)
    def benchmark_create(
        book_id: str,
        payload: BenchmarkCreate,
        actor=Depends(actor_dependency),
        quality=Depends(service),
    ):
        return BenchmarkService(quality).create(actor, book_id, payload)

    @router.get("/benchmarks/{benchmark_id}")
    def benchmark(
        book_id: str, benchmark_id: str, actor=Depends(actor_dependency), quality=Depends(service)
    ):
        return BenchmarkService(quality).get(actor, book_id, benchmark_id)

    @router.post("/benchmarks/{benchmark_id}/compare")
    def benchmark_compare(
        book_id: str,
        benchmark_id: str,
        payload: BenchmarkCompare,
        actor=Depends(actor_dependency),
        quality=Depends(service),
    ):
        return BenchmarkService(quality).compare(actor, book_id, benchmark_id, payload)

    @router.get("")
    def overview(
        book_id: str,
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1_000_000),
        actor=Depends(actor_dependency),
        quality=Depends(service),
    ):
        return quality.overview(actor, book_id, limit, offset)

    @router.get("/report")
    def report(book_id: str, actor=Depends(actor_dependency), quality=Depends(service)):
        return quality.report(actor, book_id)

    @router.post("/standards", status_code=201)
    def create(
        book_id: str,
        payload: StandardCreate,
        actor=Depends(actor_dependency),
        quality=Depends(service),
    ):
        return quality.create_standard(actor, book_id, payload)

    @router.get("/candidates/{sample_id}")
    def preview(
        book_id: str, sample_id: str, actor=Depends(actor_dependency), quality=Depends(service)
    ):
        return quality.preview(actor, book_id, sample_id)

    @router.get("/standards/{standard_id}")
    def standard(
        book_id: str, standard_id: str, actor=Depends(actor_dependency), quality=Depends(service)
    ):
        return quality.standard(actor, book_id, standard_id)

    @router.post("/standards/{standard_id}/confirm")
    def confirm(
        book_id: str,
        standard_id: str,
        payload: StandardConfirm,
        actor=Depends(actor_dependency),
        quality=Depends(service),
    ):
        return quality.confirm(actor, book_id, standard_id, payload)

    @router.post("/runs", status_code=201)
    def create_run(
        book_id: str, payload: RunCreate, actor=Depends(actor_dependency), quality=Depends(service)
    ):
        return quality.create_run(actor, book_id, payload)

    @router.get("/runs/{run_id}")
    def run(book_id: str, run_id: str, actor=Depends(actor_dependency), quality=Depends(service)):
        return quality.run(actor, book_id, run_id)

    return router
