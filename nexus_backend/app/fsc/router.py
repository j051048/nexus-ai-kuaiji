from fastapi import APIRouter, Depends, Request

from .advertising import AdvertisingDraftWrite
from .contracts import ContractConfirm, ContractWrite
from .evidence import EvidenceEvaluate, EvidencePolicyWrite, EvidenceRequest, PolicyConfirm
from .imports import ImportConfirm, ImportEnvelopeWrite
from .profiles import ProfileConfirm, ProfileWrite


def build_fsc_router(actor_dependency):
    router = APIRouter(prefix="/api/fsc", tags=["fsc"])

    def service(request: Request):
        return request.app.state.fsc_profiles

    def organization(request: Request):
        return request.app.state.fsc_organization

    def contracts(request: Request):
        return request.app.state.fsc_contracts

    def imports(request: Request):
        return request.app.state.fsc_imports

    def advertising(request: Request):
        return request.app.state.fsc_advertising

    def evidence(request: Request):
        return request.app.state.fsc_evidence

    @router.get("/evidence/active")
    def active_evidence_policies(request: Request, actor=Depends(actor_dependency)):
        return evidence(request).active_matrix(actor)

    @router.get("/evidence/policies")
    def evidence_policies(request: Request, company_id: str, actor=Depends(actor_dependency)):
        return evidence(request).list(actor, company_id)

    @router.post("/evidence/policies")
    def create_evidence_policy(
        request: Request,
        company_id: str,
        payload: EvidencePolicyWrite,
        actor=Depends(actor_dependency),
    ):
        return evidence(request).create_draft(actor, company_id, payload)

    @router.post("/evidence/policies/{policy_id}/confirm")
    def confirm_evidence_policy(
        request: Request, policy_id: str, payload: PolicyConfirm, actor=Depends(actor_dependency)
    ):
        return evidence(request).confirm(actor, policy_id, payload.expected_version)

    @router.post("/evidence/policies/{policy_id}/activate")
    def activate_evidence_policy(
        request: Request, policy_id: str, payload: PolicyConfirm, actor=Depends(actor_dependency)
    ):
        return evidence(request).activate(actor, policy_id, payload.expected_version)

    @router.post("/evidence/policies/{policy_id}/deactivate")
    def deactivate_evidence_policy(
        request: Request, policy_id: str, actor=Depends(actor_dependency)
    ):
        return evidence(request).deactivate(actor, policy_id)

    @router.post("/books/{book_id}/evidence/evaluate")
    def evaluate_evidence(
        request: Request, book_id: str, payload: EvidenceEvaluate, actor=Depends(actor_dependency)
    ):
        return evidence(request).evaluate(actor, book_id, payload)

    @router.post("/books/{book_id}/evidence/request-missing")
    def request_missing_evidence(
        request: Request, book_id: str, payload: EvidenceRequest, actor=Depends(actor_dependency)
    ):
        return evidence(request).request_missing(actor, book_id, payload)

    @router.get("/organization")
    def organization_matrix(request: Request, actor=Depends(actor_dependency)):
        return organization(request).matrix(actor)

    @router.get("/profiles/matrix")
    def matrix(request: Request, actor=Depends(actor_dependency)):
        return service(request).matrix(actor)

    @router.get("/profiles/active")
    def active(request: Request, company_id: str, actor=Depends(actor_dependency)):
        return {"profile": service(request).active(actor, company_id)}

    @router.get("/profiles")
    def list_profiles(request: Request, company_id: str, actor=Depends(actor_dependency)):
        return service(request).list_profiles(actor, company_id)

    @router.post("/profiles")
    def create(
        request: Request, company_id: str, payload: ProfileWrite, actor=Depends(actor_dependency)
    ):
        return service(request).create_draft(actor, company_id, payload)

    @router.post("/profiles/{profile_id}/confirm")
    def confirm(
        request: Request, profile_id: str, payload: ProfileConfirm, actor=Depends(actor_dependency)
    ):
        return service(request).confirm(actor, profile_id, payload)

    @router.get("/contracts/active")
    def active_contracts(request: Request, actor=Depends(actor_dependency)):
        return contracts(request).active_matrix(actor)

    @router.get("/contracts/matrix")
    def capability_matrix(request: Request, actor=Depends(actor_dependency)):
        return contracts(request).capability_matrix(actor)

    @router.get("/contracts")
    def list_contracts(request: Request, company_id: str, actor=Depends(actor_dependency)):
        return contracts(request).list(actor, company_id)

    @router.post("/contracts")
    def create_contract(
        request: Request, company_id: str, payload: ContractWrite, actor=Depends(actor_dependency)
    ):
        return contracts(request).create_draft(actor, company_id, payload)

    @router.post("/contracts/{contract_id}/confirm")
    def confirm_contract(
        request: Request,
        contract_id: str,
        payload: ContractConfirm,
        actor=Depends(actor_dependency),
    ):
        return contracts(request).confirm(actor, contract_id, payload.expected_version)

    @router.post("/contracts/{contract_id}/activate")
    def activate_contract(
        request: Request,
        contract_id: str,
        payload: ContractConfirm,
        actor=Depends(actor_dependency),
    ):
        return contracts(request).activate(actor, contract_id, payload.expected_version)

    @router.post("/contracts/{contract_id}/deactivate")
    def deactivate_contract(request: Request, contract_id: str, actor=Depends(actor_dependency)):
        return contracts(request).deactivate(actor, contract_id)

    @router.get("/imports")
    def list_imports(
        request: Request, book_id: str, period: str | None = None, actor=Depends(actor_dependency)
    ):
        return imports(request).list(actor, book_id, period)

    @router.post("/books/{book_id}/imports")
    def import_envelope(
        request: Request,
        book_id: str,
        payload: ImportEnvelopeWrite,
        actor=Depends(actor_dependency),
    ):
        return imports(request).import_envelope(actor, book_id, payload)

    @router.get("/imports/recent")
    def recent_imports(request: Request, limit: int = 20, actor=Depends(actor_dependency)):
        return imports(request).recent(actor, limit)

    @router.get("/imports/{envelope_id}")
    def import_detail(request: Request, envelope_id: str, actor=Depends(actor_dependency)):
        return imports(request).detail(actor, envelope_id)

    @router.get("/imports/{envelope_id}/rows/{row_number}/receipt")
    def import_row_receipt(
        request: Request, envelope_id: str, row_number: int, actor=Depends(actor_dependency)
    ):
        return imports(request).row_receipt(actor, envelope_id, row_number)

    @router.post("/imports/{envelope_id}/confirm")
    def confirm_import(
        request: Request, envelope_id: str, payload: ImportConfirm, actor=Depends(actor_dependency)
    ):
        return imports(request).confirm(actor, envelope_id, payload.expected_version)

    @router.post("/books/{book_id}/advertising/drafts")
    def advertising_draft(
        request: Request,
        book_id: str,
        payload: AdvertisingDraftWrite,
        actor=Depends(actor_dependency),
    ):
        return advertising(request).generate(actor, book_id, payload)

    @router.get("/books/{book_id}/advertising/drafts")
    def advertising_drafts(request: Request, book_id: str, actor=Depends(actor_dependency)):
        return advertising(request).list(actor, book_id)

    return router
