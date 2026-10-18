from fastapi import APIRouter, Depends, Query, Request

from .models import (
    AccountCreate,
    AccountRevision,
    MappingCreate,
    OpeningCommand,
    OpeningImport,
    OpeningPackageCreate,
    PolicyCreate,
)


def build_opening_router(actor_dependency):
    router = APIRouter(prefix="/api/opening", tags=["B02期初准备"])

    def service(request: Request):
        return request.app.state.opening

    @router.post("/policies", status_code=201)
    def create_policy(payload: PolicyCreate, current=Depends(actor_dependency), opening=Depends(service)):
        return opening.create_policy(current, payload)

    @router.get("/policies")
    def list_policies(
        ledger_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return {"policies": opening.list_policies(current, ledger_id)}

    @router.post("/accounts", status_code=201)
    def create_account(payload: AccountCreate, current=Depends(actor_dependency), opening=Depends(service)):
        return opening.create_account(current, payload)

    @router.post("/accounts/{account_id}/versions", status_code=201)
    def revise_account(
        account_id: str,
        payload: AccountRevision,
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return opening.revise_account(current, account_id, payload)

    @router.get("/accounts")
    def list_accounts(
        ledger_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return {"accounts": opening.list_accounts(current, ledger_id)}

    @router.post("/mappings", status_code=201)
    def create_mapping(payload: MappingCreate, current=Depends(actor_dependency), opening=Depends(service)):
        return opening.create_mapping(current, payload)

    @router.get("/mappings")
    def list_mappings(
        ledger_id: str = Query(min_length=1, max_length=100),
        book_id: str | None = Query(default=None, max_length=100),
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return {"mappings": opening.list_mappings(current, ledger_id, book_id)}

    @router.post("/packages", status_code=201)
    def create_package(
        payload: OpeningPackageCreate,
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return opening.create_package(current, payload)

    @router.get("/packages")
    def list_packages(
        ledger_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return {"packages": opening.list_packages(current, ledger_id)}

    @router.post("/packages/{package_id}/versions", status_code=201)
    def import_version(
        package_id: str,
        payload: OpeningImport,
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return opening.import_version(current, package_id, payload)

    @router.post("/versions/{version_id}/validate")
    def validate_version(
        version_id: str,
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return opening.validate_version(current, version_id)

    @router.post("/versions/{version_id}/submit")
    def submit_version(
        version_id: str,
        payload: OpeningCommand,
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return opening.submit_version(current, version_id, payload)

    @router.post("/versions/{version_id}/approve")
    def approve_version(
        version_id: str,
        payload: OpeningCommand,
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return opening.approve_version(current, version_id, payload)

    @router.post("/versions/{version_id}/freeze")
    def freeze_version(
        version_id: str,
        payload: OpeningCommand,
        current=Depends(actor_dependency),
        opening=Depends(service),
    ):
        return opening.freeze_version(current, version_id, payload)

    return router
