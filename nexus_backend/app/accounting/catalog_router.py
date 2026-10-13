from fastapi import APIRouter, Depends, Request

from .catalog_models import (
    AccountDefinition,
    AccountImportRequest,
    AccountUpdate,
    TemplateDefinition,
    TemplatePreviewRequest,
    TemplateUpdate,
)


def build_catalog_router(actor_dependency):
    router = APIRouter(prefix="/api/books/{book_id}/catalog", tags=["客户科目与记账模板"])

    def catalog(request: Request):
        return request.app.state.catalog

    @router.get("")
    def get(book_id: str, current=Depends(actor_dependency), service=Depends(catalog)):
        return service.get(current, book_id)

    @router.post("/accounts", status_code=201)
    def create_account(
        book_id: str,
        payload: AccountDefinition,
        current=Depends(actor_dependency),
        service=Depends(catalog),
    ):
        return service.create_account(current, book_id, payload)

    @router.post("/accounts/import/preview")
    def preview_import(
        book_id: str,
        payload: AccountImportRequest,
        current=Depends(actor_dependency),
        service=Depends(catalog),
    ):
        return service.import_accounts(current, book_id, payload, preview=True)

    @router.post("/accounts/import")
    def import_accounts(
        book_id: str,
        payload: AccountImportRequest,
        current=Depends(actor_dependency),
        service=Depends(catalog),
    ):
        return service.import_accounts(current, book_id, payload)

    @router.put("/accounts/{code}")
    def update_account(
        book_id: str,
        code: str,
        payload: AccountUpdate,
        current=Depends(actor_dependency),
        service=Depends(catalog),
    ):
        return service.update_account(current, book_id, code, payload)

    @router.post("/templates/preview")
    def preview_template(
        book_id: str,
        payload: TemplatePreviewRequest,
        current=Depends(actor_dependency),
        service=Depends(catalog),
    ):
        return service.preview_template(current, book_id, payload)

    @router.post("/templates", status_code=201)
    def create_template(
        book_id: str,
        payload: TemplateDefinition,
        current=Depends(actor_dependency),
        service=Depends(catalog),
    ):
        return service.save_template(current, book_id, payload)

    @router.put("/templates/{template_id}")
    def update_template(
        book_id: str,
        template_id: str,
        payload: TemplateUpdate,
        current=Depends(actor_dependency),
        service=Depends(catalog),
    ):
        return service.save_template(current, book_id, payload, template_id)

    return router
