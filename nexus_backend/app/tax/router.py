import base64
import hashlib
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from . import filings
from .readiness import filing_readiness
from .workpaper import TaxWorkpaperService
from .context import tax_context
from .pilot import PilotBoundaryService
from .sources import TaxSourceAdapter
from .fees import TaxFeeService
from .costs import (
    TaxCostGenerate,
    TaxCostInvalidate,
    TaxCostPolicyWrite,
    TaxCostReview,
    TaxCostService,
    TaxCostSourceWrite,
)
from .leases import (
    LeaseAmend,
    LeaseConfirmation,
    LeaseCreate,
    LeasePolicy,
    LeaseRecognition,
    LeaseRefund,
    LeaseService,
)
from .basis import TaxBasisGenerate, TaxBasisReview, TaxBasisService




class TaxSourceRowWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["adopted", "excluded", "needs_review"]
    reason: str = Field(default="", max_length=1000)


class LeaseTerminateWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=1000)


class TaxSourceImportWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=150)
    content_base64: str = Field(min_length=1, max_length=16_000_000)
    source_system: str = Field(min_length=1, max_length=80)
    external_id: str = Field(min_length=1, max_length=200)
    version: StrictInt = Field(ge=1)
    sheet_name: str = Field(min_length=1, max_length=120)
    column_map: dict[str, str]
    rule_version: str = Field(min_length=1, max_length=80)


class TaxSourceBatchWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files: list[TaxSourceImportWrite] = Field(min_length=1, max_length=20)


class TaxFeePolicyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=80)
    driver_receivable_account: str = Field(min_length=1, max_length=20)
    vehicle_revenue_account: str = Field(min_length=1, max_length=20)


class TaxFeeGenerateWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str = Field(min_length=7, max_length=7)
    source_file_ids: list[str] = Field(min_length=1, max_length=20)
    policy: TaxFeePolicyWrite


class TaxFeeReviewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1000)


class TaxFeeInvalidateWrite(TaxFeeReviewWrite):
    pass


class TaxContributionLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_line_id: str = Field(min_length=1, max_length=100)
    amount_cents: StrictInt
    quantity_milli: StrictInt = Field(default=0, ge=0)


class TaxContributionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_number: StrictInt = Field(ge=2)
    amount_cents: StrictInt
    quantity_milli: StrictInt = Field(default=0, ge=0)
    lines: list[TaxContributionLine] = Field(default_factory=list, max_length=100)


class TaxSourceContributionWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    output_kind: Literal["workpaper", "preview", "approval"]
    output_id: str = Field(min_length=1, max_length=100)
    output_version: StrictInt = Field(ge=1)
    contributions: list[TaxContributionRow] = Field(min_length=1, max_length=500)


class TaxProfileWrite(BaseModel):
    book_id: str = Field(min_length=1, max_length=100)
    taxpayer_type: str = Field(min_length=1, max_length=32)
    vat_rate_bp: StrictInt = Field(ge=0, le=100_000)
    surtax_rate_bp: StrictInt = Field(default=0, ge=0, le=100_000)
    filing_cycle: str = Field(default="monthly", max_length=16)
    effective_from: str = Field(pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    reason: str = Field(min_length=3, max_length=1000)


class TaxAccountRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=100)
    account_version: StrictInt = Field(ge=1)
    role: Literal["vat_payable", "excluded"]


class TaxWorkpaperCreate(BaseModel):
    book_id: str = Field(min_length=1, max_length=100)
    period: str | None = Field(default=None, pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    reason: str = Field(default="", max_length=1000)
    vat_accounts: list[TaxAccountRole] = Field(default_factory=list, max_length=1000)


class TaxFilingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    book_id: str = Field(min_length=1, max_length=100)
    period: str | None = Field(default=None, pattern=r"^20[0-9]{2}-(0[1-9]|1[0-2])$")
    kind: Literal["vat", "surtax"]
    status: Literal["filed", "paid"]
    external_ref: str = Field(min_length=3, max_length=100)
    occurred_at: str = Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
    amount_cents: StrictInt = Field(default=0, ge=0)
    evidence_note: str = Field(default="", max_length=500)
    entry_kind: Literal["registration", "correction", "void"] = "registration"
    supersedes_id: str | None = Field(default=None, max_length=100)
    correction_reason: str = Field(default="", max_length=1000)


class TaxFilingAttach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str = Field(min_length=1, max_length=100)


class PilotConfigWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    authority_mode: Literal["internal", "tplus_voucher", "hybrid"]
    source_of_truth: str | None = Field(default=None, max_length=200)
    tplus_version: str | None = Field(default=None, max_length=80)
    template_sha256: str | None = Field(default=None, max_length=64)
    voucher_word: Literal["记", "转", "收", "付"] | None = None
    numbering_owner: str | None = Field(default=None, max_length=80)
    reporting_mode: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=2000)


class PilotDependencyWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=1, max_length=60)
    status: Literal["unresolved", "confirmed", "not_applicable"]
    owner_role: str = Field(default="客户会计", max_length=80)
    evidence_ref: str = Field(default="", max_length=500)
    impact: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=1000)


class PilotDraftWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt = Field(ge=0)
    config: PilotConfigWrite
    dependencies: list[PilotDependencyWrite] = Field(min_length=1, max_length=10)


class PilotConfirmWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt = Field(ge=1)


class PilotPreviewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["tax_preview"] = "tax_preview"


class TaxWorkpaperReview(BaseModel):
    book_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=1000)
    expected_version: StrictInt = Field(ge=1)


def build_tax_router(actor_dependency):
    router = APIRouter(prefix="/api/tax", tags=["B05税务底稿"])

    def service(request: Request) -> TaxWorkpaperService:
        return request.app.state.tax

    @router.get("/context")
    def preparation_context(
        book_id: str = Query(min_length=1, max_length=100),
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax_context(tax, current, book_id, period)

    @router.put("/profile")
    def write_profile(
        payload: TaxProfileWrite,
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax.set_profile(current, payload.book_id, payload.model_dump())

    @router.get("/readiness")
    def filing_setup(
        book_id: str = Query(min_length=1, max_length=100),
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return filing_readiness(tax, current, book_id, period)

    @router.post("/filings", status_code=201)
    def record_filing(
        payload: TaxFilingWrite,
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return filings.record(tax, current, payload.model_dump())

    @router.get("/filings")
    def list_filings(
        book_id: str = Query(min_length=1, max_length=100),
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return {"filings": filings.list_for(tax, current, book_id, period)}

    @router.post("/filings/{filing_id}/attachments", status_code=201)
    def attach_filing_original(
        filing_id: str,
        payload: TaxFilingAttach,
        request: Request,
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return filings.attach(request.app.state.uploads, tax, current, filing_id, payload.upload_id)

    @router.get("/filings/{filing_id}/attachments/{attachment_id}")
    def download_filing_original(
        filing_id: str,
        attachment_id: str,
        request: Request,
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        metadata = filings.attachment_for(tax, current, filing_id, attachment_id)
        data = request.app.state.attachment_store.read(metadata)
        return Response(
            data,
            media_type=metadata["media_type"],
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(metadata['filename'], safe='')}",
                "Content-Security-Policy": "sandbox; default-src 'none'",
                "Cache-Control": "private, no-store",
                "X-Content-Sha256": metadata["sha256"],
            },
        )

    @router.get("/profile")
    def read_profile(
        book_id: str = Query(min_length=1, max_length=100),
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return {"profile": tax.profile(current, book_id, period)}

    @router.post("/workpapers", status_code=201)
    def create_workpaper(
        payload: TaxWorkpaperCreate,
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax.generate(
            current,
            payload.book_id,
            payload.period,
            payload.reason,
            [item.model_dump() for item in payload.vat_accounts],
        )

    @router.get("/workpapers")
    def list_workpapers(
        book_id: str = Query(min_length=1, max_length=100),
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return {"workpapers": tax.list(current, book_id, period)["items"]}

    @router.get("/workpapers/{workpaper_id}")
    def read_workpaper(
        workpaper_id: str,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax.get(current, book_id, workpaper_id)

    @router.post("/workpapers/{workpaper_id}/review")
    def review_workpaper(
        workpaper_id: str,
        payload: TaxWorkpaperReview,
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax.review(
            current, payload.book_id, workpaper_id, payload.reason, payload.expected_version
        )

    @router.get("/workpapers/{workpaper_id}/csv")
    def export_workpaper(
        workpaper_id: str,
        book_id: str = Query(min_length=1, max_length=100),
        expected_version: int = Query(ge=1),
        source_digest: str = Query(pattern=r"^[0-9a-f]{64}$"),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        body = tax.csv(current, book_id, workpaper_id, expected_version, source_digest)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="tax-{workpaper_id}.csv"',
                "X-Content-Sha256": digest,
                "X-Workpaper-Version": str(expected_version),
                "X-Source-Sha256": source_digest,
                "Cache-Control": "no-store",
            },
        )


    def pilot_service(request: Request) -> PilotBoundaryService:
        return request.app.state.tax_pilot

    def source_service(request: Request) -> TaxSourceAdapter:
        return request.app.state.tax_sources

    def fee_service(request: Request) -> TaxFeeService:
        return request.app.state.tax_fees

    def cost_service(request: Request) -> TaxCostService:
        return request.app.state.tax_costs

    def lease_service(request: Request) -> LeaseService:
        return request.app.state.tax_leases

    def basis_service(request: Request) -> TaxBasisService:
        return request.app.state.tax_basis

    @router.post("/books/{book_id}/leases", status_code=201)
    def create_lease(book_id: str, payload: LeaseCreate, current=Depends(actor_dependency),
                     leases=Depends(lease_service)):
        return leases.create(current, book_id, payload)

    @router.get("/books/{book_id}/leases")
    def list_leases(book_id: str, current=Depends(actor_dependency), leases=Depends(lease_service)):
        return leases.list(current, book_id)

    @router.get("/books/{book_id}/leases/{contract_id}")
    def get_lease(book_id: str, contract_id: str, current=Depends(actor_dependency),
                  leases=Depends(lease_service)):
        return leases.get(current, book_id, contract_id)

    @router.post("/books/{book_id}/leases/{contract_id}/confirm", status_code=201)
    def confirm_lease(book_id: str, contract_id: str, payload: LeaseConfirmation,
                      current=Depends(actor_dependency), leases=Depends(lease_service)):
        return leases.confirm(current, book_id, contract_id, payload)

    @router.post("/books/{book_id}/leases/{contract_id}/recognize", status_code=201)
    def recognize_lease(book_id: str, contract_id: str, payload: LeaseRecognition,
                        current=Depends(actor_dependency), leases=Depends(lease_service)):
        return leases.recognize(current, book_id, contract_id, payload)

    @router.post("/books/{book_id}/leases/{contract_id}/refund", status_code=201)
    def refund_lease(book_id: str, contract_id: str, payload: LeaseRefund,
                     current=Depends(actor_dependency), leases=Depends(lease_service)):
        return leases.refund(current, book_id, contract_id, payload)

    @router.post("/books/{book_id}/leases/{contract_id}/amend")
    def amend_lease(book_id: str, contract_id: str, payload: LeaseAmend,
                    current=Depends(actor_dependency), leases=Depends(lease_service)):
        return leases.amend(current, book_id, contract_id, payload)

    @router.post("/books/{book_id}/leases/{contract_id}/terminate")
    def terminate_lease(book_id: str, contract_id: str, payload: LeaseTerminateWrite,
                        current=Depends(actor_dependency), leases=Depends(lease_service)):
        return leases.terminate(current, book_id, contract_id, payload.reason)

    @router.post("/books/{book_id}/tax-basis", status_code=201)
    def generate_tax_basis(book_id: str, payload: TaxBasisGenerate, current=Depends(actor_dependency),
                           basis=Depends(basis_service)):
        return basis.generate(current, book_id, payload)

    @router.get("/books/{book_id}/tax-basis")
    def list_tax_basis(book_id: str, period: str = Query(min_length=7, max_length=7),
                       current=Depends(actor_dependency), basis=Depends(basis_service)):
        return basis.list(current, book_id, period)

    @router.get("/books/{book_id}/tax-basis/{review_id}")
    def get_tax_basis(book_id: str, review_id: str, current=Depends(actor_dependency),
                      basis=Depends(basis_service)):
        return basis.get(current, book_id, review_id)

    @router.post("/books/{book_id}/tax-basis/{review_id}/review")
    def review_tax_basis(book_id: str, review_id: str, payload: TaxBasisReview,
                         current=Depends(actor_dependency), basis=Depends(basis_service)):
        return basis.review(current, book_id, review_id, payload)

    @router.post("/books/{book_id}/sources", status_code=201)
    def import_source(
        book_id: str,
        payload: TaxSourceImportWrite,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        try:
            raw = base64.b64decode(payload.content_base64, validate=True)
        except Exception:
            from ..accounting.domain import AccountingError
            raise AccountingError("文件内容不是有效的 Base64", 422) from None
        return sources.import_source(
            current,
            book_id,
            filename=payload.filename,
            data=raw,
            source_system=payload.source_system,
            external_id=payload.external_id,
            version=payload.version,
            sheet_name=payload.sheet_name,
            column_map=payload.column_map,
            rule_version=payload.rule_version,
        )

    @router.post("/books/{book_id}/sources/batch")
    def import_source_batch(
        book_id: str,
        payload: TaxSourceBatchWrite,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        entries = []
        failures = []
        for item in payload.files:
            try:
                raw = base64.b64decode(item.content_base64, validate=True)
            except Exception:
                failures.append({"status": "failed", "external_id": item.external_id,
                                 "http_status": 422, "error": "文件内容不是有效的 Base64",
                                 "retryable": True})
                continue
            entries.append({**item.model_dump(exclude={"content_base64"}), "data": raw})
        result = sources.import_batch(current, book_id, entries)
        result["items"] = failures + result["items"]
        result["failed"] += len(failures)
        return result

    @router.get("/books/{book_id}/sources/{source_id}/original")
    def download_source_original(book_id: str, source_id: str,
                                 current=Depends(actor_dependency), sources=Depends(source_service)):
        filename, content = sources.download_original(current, book_id, source_id)
        return Response(content, media_type="application/octet-stream", headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        })

    @router.patch("/books/{book_id}/sources/{source_id}/rows/{row_number}")
    def review_source_row(
        book_id: str,
        source_id: str,
        row_number: int,
        payload: TaxSourceRowWrite,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        return sources.review_row(current, book_id, source_id, row_number,
                                  status=payload.status, reason=payload.reason)

    @router.post("/books/{book_id}/sources/{source_id}/contributions", status_code=201)
    def record_source_contributions(
        book_id: str,
        source_id: str,
        payload: TaxSourceContributionWrite,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        return sources.record_contributions(
            current, book_id, source_id,
            output_kind=payload.output_kind, output_id=payload.output_id,
            output_version=payload.output_version,
            contributions=[item.model_dump() for item in payload.contributions],
        )

    @router.get("/books/{book_id}/sources/contributions/aggregate")
    def aggregate_source_contributions(
        book_id: str,
        output_kind: Literal["workpaper", "preview", "approval"],
        output_id: str,
        output_version: int,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        return sources.aggregate_contributions(
            current, book_id, output_kind=output_kind, output_id=output_id, output_version=output_version,
        )

    @router.get("/books/{book_id}/sources/contributions")
    def list_source_contributions(
        book_id: str,
        output_kind: Literal["workpaper", "preview", "approval"],
        output_id: str,
        output_version: int | None = None,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        return sources.list_contributions(current, book_id, output_kind=output_kind, output_id=output_id,
                                          output_version=output_version)

    @router.get("/books/{book_id}/sources")
    def list_sources(
        book_id: str,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        return sources.list_sources(current, book_id)

    @router.post("/books/{book_id}/fee-batches", status_code=201)
    def generate_fee_batch(
        book_id: str,
        payload: TaxFeeGenerateWrite,
        current=Depends(actor_dependency),
        fees=Depends(fee_service),
    ):
        return fees.generate(current, book_id, period=payload.period,
                            source_file_ids=payload.source_file_ids,
                            policy=payload.policy.model_dump())

    @router.get("/books/{book_id}/fee-batches")
    def list_fee_batches(book_id: str, current=Depends(actor_dependency), fees=Depends(fee_service)):
        return fees.list_batches(current, book_id)

    @router.get("/books/{book_id}/fee-batches/{batch_id}")
    def get_fee_batch(book_id: str, batch_id: str, current=Depends(actor_dependency), fees=Depends(fee_service)):
        return fees.get(current, book_id, batch_id)

    @router.post("/books/{book_id}/fee-batches/{batch_id}/review")
    def review_fee_batch(book_id: str, batch_id: str, payload: TaxFeeReviewWrite,
                         current=Depends(actor_dependency), fees=Depends(fee_service)):
        return fees.review(current, book_id, batch_id, expected_version=payload.expected_version, reason=payload.reason)

    @router.post("/books/{book_id}/fee-batches/{batch_id}/validate")
    def validate_fee_batch(book_id: str, batch_id: str, current=Depends(actor_dependency), fees=Depends(fee_service)):
        return fees.validate(current, book_id, batch_id)

    @router.post("/books/{book_id}/fee-batches/{batch_id}/invalidate")
    def invalidate_fee_batch(book_id: str, batch_id: str, payload: TaxFeeInvalidateWrite,
                             current=Depends(actor_dependency), fees=Depends(fee_service)):
        return fees.invalidate(current, book_id, batch_id, expected_version=payload.expected_version, reason=payload.reason)

    @router.post("/books/{book_id}/cost-sources", status_code=201)
    def add_cost_source(book_id: str, payload: TaxCostSourceWrite,
                        current=Depends(actor_dependency), costs=Depends(cost_service)):
        return costs.add_source(current, book_id, payload)

    @router.get("/books/{book_id}/cost-sources")
    def list_cost_sources(book_id: str, kind: Literal["repair", "insurance", "oa"] | None = None,
                          period: str | None = None, current=Depends(actor_dependency),
                          costs=Depends(cost_service)):
        return costs.list_sources(current, book_id, kind=kind, period=period)

    @router.post("/books/{book_id}/cost-batches", status_code=201)
    def generate_cost_batch(book_id: str, payload: TaxCostGenerate,
                            current=Depends(actor_dependency), costs=Depends(cost_service)):
        return costs.generate(current, book_id, payload)

    @router.get("/books/{book_id}/cost-batches")
    def list_cost_batches(book_id: str, kind: Literal["repair", "insurance", "oa"] | None = None,
                          current=Depends(actor_dependency), costs=Depends(cost_service)):
        return costs.list_batches(current, book_id, kind=kind)

    @router.get("/books/{book_id}/cost-batches/{batch_id}")
    def get_cost_batch(book_id: str, batch_id: str, current=Depends(actor_dependency),
                       costs=Depends(cost_service)):
        return costs.get_batch(current, book_id, batch_id)

    @router.post("/books/{book_id}/cost-batches/{batch_id}/validate")
    def validate_cost_batch(book_id: str, batch_id: str, current=Depends(actor_dependency),
                            costs=Depends(cost_service)):
        return costs.validate(current, book_id, batch_id)

    @router.post("/books/{book_id}/cost-batches/{batch_id}/review")
    def review_cost_batch(book_id: str, batch_id: str, payload: TaxCostReview,
                          current=Depends(actor_dependency), costs=Depends(cost_service)):
        return costs.review(current, book_id, batch_id, payload)

    @router.post("/books/{book_id}/cost-batches/{batch_id}/invalidate")
    def invalidate_cost_batch(book_id: str, batch_id: str, payload: TaxCostInvalidate,
                              current=Depends(actor_dependency), costs=Depends(cost_service)):
        return costs.invalidate(current, book_id, batch_id, payload)

    @router.get("/pilot/companies/{company_id}/readiness")
    def pilot_readiness(
        company_id: str,
        current=Depends(actor_dependency),
        pilot=Depends(pilot_service),
    ):
        return pilot.readiness(current, company_id)

    @router.post("/pilot/companies/{company_id}/draft", status_code=201)
    def pilot_draft(
        company_id: str,
        payload: PilotDraftWrite,
        current=Depends(actor_dependency),
        pilot=Depends(pilot_service),
    ):
        body = payload.model_dump(exclude_none=True)
        return pilot.update(
            current,
            company_id,
            body["expected_version"],
            body["config"],
            body["dependencies"],
        )

    @router.post("/pilot/companies/{company_id}/confirm")
    def pilot_confirm(
        company_id: str,
        payload: PilotConfirmWrite,
        current=Depends(actor_dependency),
        pilot=Depends(pilot_service),
    ):
        return pilot.confirm(current, company_id, payload.expected_version)

    @router.post("/pilot/companies/{company_id}/preview", status_code=201)
    def pilot_preview(
        company_id: str,
        payload: PilotPreviewWrite,
        current=Depends(actor_dependency),
        pilot=Depends(pilot_service),
    ):
        return pilot.preview(current, company_id, payload.purpose)

    @router.post("/pilot/companies/{company_id}/previews/{preview_id}/validate")
    def pilot_validate_preview(
        company_id: str,
        preview_id: str,
        current=Depends(actor_dependency),
        pilot=Depends(pilot_service),
    ):
        return pilot.validate_preview(current, company_id, preview_id)

    return router
