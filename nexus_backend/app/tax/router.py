import base64
import calendar
import hashlib
from datetime import date
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from . import filings
from .readiness import filing_readiness
from .workpaper import TaxWorkpaperService
from .policy_items import TaxPolicyItemService, TaxPolicyItemWrite
from .context import tax_context
from .pilot import PilotBoundaryService
from .sources import TaxSourceAdapter
from .fees import TaxFeeService
from .chain import FeeChainView
from .variances import FeeVarianceService
from .packages import (
    PackageConsume,
    PackageContractDraft,
    PackageRelease,
    PackageVersion,
    RepairPackageService,
)
from .costs import (
    TaxCostGenerate,
    TaxCostInvalidate,
    TaxCostReview,
    TaxCostService,
    TaxCostSourceWrite,
    RepairRateDeactivate,
    RepairRateDraft,
    RepairRateService,
    RepairRateWrite,
)
from .leases import (
    LeaseAmend,
    LeaseApprove,
    LeaseConfirmation,
    LeaseCreate,
    LeaseInvoiceRed,
    LeaseRecognition,
    LeaseRefund,
    LeaseService,
)
from .basis import TaxBasisGenerate, TaxBasisReview, TaxBasisService


class TaxSourceRowWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["adopted", "excluded", "needs_review"]
    reason: str = Field(default="", max_length=1000)


class RepairRateVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: StrictInt = Field(ge=1)


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


class TaxSourceOriginalAttach(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str = Field(min_length=1, max_length=100)


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


class FeeAllocationWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bank_source_id: str = Field(min_length=1, max_length=100)
    amount_cents: StrictInt = Field(gt=0)
    idempotency_key: str = Field(min_length=1, max_length=100)
    payer_name: str | None = Field(default=None, max_length=100)
    collection_basis: str | None = Field(default=None, max_length=500)
    bank_statement_line_id: str | None = Field(default=None, max_length=100)


class FeeAllocationReverse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=200)


class FeeRefundWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bank_source_id: str = Field(min_length=1, max_length=100)
    amount_cents: StrictInt = Field(gt=0)
    reason: str = Field(min_length=3, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=100)


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


class FeeVarianceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal[
        "underpaid", "overpaid", "unmatched", "cross_period", "many_to_one", "one_to_many"
    ]
    expected_cents: StrictInt
    actual_cents: StrictInt
    receivable_id: str | None = Field(default=None, max_length=100)
    bank_source_id: str | None = Field(default=None, max_length=100)
    settlement_source_id: str | None = Field(default=None, max_length=100)
    fee_source_id: str | None = Field(default=None, max_length=100)
    evidence_ref: str | None = Field(default=None, max_length=500)
    idempotency_key: str = Field(min_length=1, max_length=200)


class FeeVarianceHandle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str = Field(min_length=1, max_length=100)
    evidence_ref: str | None = Field(default=None, max_length=500)


class FeeVarianceReverse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=1000)


class FeeVarianceAssign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignee_id: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=3, max_length=500)


def _period_end(period):
    """Last calendar day of ``YYYY-MM``; vch date defaults must stay in period."""
    year, month = int(period[:4]), int(period[5:7])
    return f"{period}-{calendar.monthrange(year, month)[1]:02d}"


def build_tax_router(actor_dependency):
    router = APIRouter(prefix="/api/tax", tags=["B05税务底稿"])

    def service(request: Request) -> TaxWorkpaperService:
        return request.app.state.tax

    def variances(request: Request) -> FeeVarianceService:
        return request.app.state.fee_variances

    def poster(request: Request):
        return request.app.state.posting

    def manual_reviews(request: Request):
        from ..posting.manual_review import ManualReviewService

        return ManualReviewService(request.app.state.service.db)

    def four_tax_package_for(posting, tax, actor, payload, book):
        """Compose the four-tax package the same way for preview and for posting.

        Preview and request must never show different numbers: both derive the
        surtax basis the same way, and both refuse when that basis cannot
        reproduce what the reviewed workpaper already recorded.
        """
        from ..accounting.domain import AccountingError
        from . import four_tax

        with posting.db.transaction(actor=actor) as conn:
            profile = tax._profile(conn, book, book["period"])

        def compose(surtax_items):
            return four_tax.package(
                tax,
                actor,
                payload.book_id,
                payload.period,
                surtax_items=surtax_items,
                cumulative_profit_cents=payload.cumulative_profit_cents,
                loss_carryforward_cents=payload.loss_carryforward_cents,
                prepaid_cents=payload.prepaid_cents,
                cit_rate_bp=payload.cit_rate_bp,
                halved=payload.halved,
                stamp_contracts=payload.stamp_contracts,
                stamp_rates=payload.stamp_rates,
                stamp_zero_confirmed=payload.stamp_zero_confirmed,
            )

        if payload.surtax_items:
            basis = {"source": "request", "rate_bp": None, "items": len(payload.surtax_items)}
            package = compose(payload.surtax_items)
        elif profile is not None:
            # The registered profile supplies the very rate the workpaper used, and
            # the response says so. Inventing a rate would be guessing.
            rate = int(profile["surtax_rate_bp"])
            basis = {
                "source": "tax_profile",
                "rate_bp": rate,
                "profile_id": profile["id"],
                "effective_from": profile["effective_from"],
            }
            package = compose([{"code": "profile_surtax", "rate_bp": rate}])
        else:
            # Neither an explicit item list nor a registered profile. Let the
            # package's own guards speak in their natural order: the missing
            # reviewed workpaper is the real blocker when there is none, and
            # otherwise the missing surtax policy items are.
            compose([])
            raise AssertionError("unreachable: compose([]) 必然抛出 409")
        recorded = int(package["vat"]["surtax_cents_recorded"])
        computed = int(package["surtax"]["total_cents"])
        if computed != recorded:
            raise AccountingError(
                f"本次计提的附加税 {computed} 分与已复核底稿登记的 {recorded} 分不一致；"
                "请核对附加税政策条目后再提交，不能按两套依据入账",
                409,
            )
        return package, basis

    @router.get("/context")
    def preparation_context(
        book_id: str = Query(min_length=1, max_length=100),
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        return tax_context(tax, current, book_id, period)

    @router.get("/books/{book_id}/cumulative-profit")
    def cumulative_profit(
        book_id: str,
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        """累计利润参考值：只读、可溯源，无来源时明确返回 null。"""

        from . import profit_source

        from ..accounting.domain import AccountingError

        with tax.db.transaction(actor=current) as conn:
            book = tax.access.book(conn, current, book_id, "read")
            target = period or book["period"]
            if target != book["period"]:
                raise AccountingError("累计利润期间必须与当前账套月份一致，跨期请交人工核验", 409)
            return profit_source.suggest(conn, book, target)

    def policy_items(request: Request) -> TaxPolicyItemService:
        return request.app.state.tax_policy_items

    class PolicyBases(BaseModel):
        model_config = ConfigDict(extra="forbid")
        period: str = Field(min_length=7, max_length=7)
        bases: dict[str, int]

    @router.get("/books/{book_id}/policy-items")
    def list_policy_items(
        book_id: str,
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        policies=Depends(policy_items),
    ):
        return policies.list(current, book_id, period)

    @router.post("/books/{book_id}/policy-items", status_code=201)
    def add_policy_items(
        book_id: str,
        payload: list[TaxPolicyItemWrite],
        current=Depends(actor_dependency),
        policies=Depends(policy_items),
    ):
        return policies.add(current, book_id, payload)

    @router.post("/books/{book_id}/policy-items/calculate")
    def calculate_policy_items(
        book_id: str,
        payload: PolicyBases,
        current=Depends(actor_dependency),
        policies=Depends(policy_items),
    ):
        return policies.calculate(current, book_id, payload.period, payload.bases)

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

    def rate_service(request: Request) -> RepairRateService:
        return request.app.state.repair_rates

    def package_service(request: Request) -> RepairPackageService:
        return request.app.state.repair_packages

    def lease_service(request: Request) -> LeaseService:
        return request.app.state.tax_leases

    def basis_service(request: Request) -> TaxBasisService:
        return request.app.state.tax_basis

    @router.post("/books/{book_id}/leases", status_code=201)
    def create_lease(
        book_id: str,
        payload: LeaseCreate,
        current=Depends(actor_dependency),
        leases=Depends(lease_service),
    ):
        return leases.create(current, book_id, payload)

    @router.get("/books/{book_id}/leases")
    def list_leases(book_id: str, current=Depends(actor_dependency), leases=Depends(lease_service)):
        return leases.list(current, book_id)

    @router.get("/books/{book_id}/leases/{contract_id}")
    def get_lease(
        book_id: str,
        contract_id: str,
        current=Depends(actor_dependency),
        leases=Depends(lease_service),
    ):
        return leases.get(current, book_id, contract_id)

    @router.post("/books/{book_id}/leases/{contract_id}/confirm", status_code=201)
    def confirm_lease(
        book_id: str,
        contract_id: str,
        payload: LeaseConfirmation,
        current=Depends(actor_dependency),
        leases=Depends(lease_service),
    ):
        return leases.confirm(current, book_id, contract_id, payload)

    @router.post("/books/{book_id}/leases/{contract_id}/recognize", status_code=201)
    def recognize_lease(
        book_id: str,
        contract_id: str,
        payload: LeaseRecognition,
        current=Depends(actor_dependency),
        leases=Depends(lease_service),
    ):
        return leases.recognize(current, book_id, contract_id, payload)

    @router.post("/books/{book_id}/leases/{contract_id}/approve")
    def approve_lease(
        book_id: str,
        contract_id: str,
        payload: LeaseApprove,
        current=Depends(actor_dependency),
        leases=Depends(lease_service),
    ):
        return leases.approve(current, book_id, contract_id, payload.expected_version)

    @router.post("/books/{book_id}/leases/{contract_id}/invoice-reds", status_code=201)
    def red_lease_invoice(
        book_id: str,
        contract_id: str,
        payload: LeaseInvoiceRed,
        current=Depends(actor_dependency),
        leases=Depends(lease_service),
    ):
        return leases.red_invoice(current, book_id, contract_id, payload)

    @router.post("/books/{book_id}/leases/{contract_id}/refund", status_code=201)
    def refund_lease(
        book_id: str,
        contract_id: str,
        payload: LeaseRefund,
        current=Depends(actor_dependency),
        leases=Depends(lease_service),
    ):
        return leases.refund(current, book_id, contract_id, payload)

    @router.post("/books/{book_id}/leases/{contract_id}/amend")
    def amend_lease(
        book_id: str,
        contract_id: str,
        payload: LeaseAmend,
        current=Depends(actor_dependency),
        leases=Depends(lease_service),
    ):
        return leases.amend(current, book_id, contract_id, payload)

    @router.post("/books/{book_id}/leases/{contract_id}/terminate")
    def terminate_lease(
        book_id: str,
        contract_id: str,
        payload: LeaseTerminateWrite,
        current=Depends(actor_dependency),
        leases=Depends(lease_service),
    ):
        return leases.terminate(current, book_id, contract_id, payload.reason)

    @router.post("/books/{book_id}/tax-basis", status_code=201)
    def generate_tax_basis(
        book_id: str,
        payload: TaxBasisGenerate,
        current=Depends(actor_dependency),
        basis=Depends(basis_service),
    ):
        return basis.generate(current, book_id, payload)

    @router.get("/books/{book_id}/tax-basis")
    def list_tax_basis(
        book_id: str,
        period: str = Query(min_length=7, max_length=7),
        current=Depends(actor_dependency),
        basis=Depends(basis_service),
    ):
        return basis.list(current, book_id, period)

    @router.get("/books/{book_id}/tax-basis/{review_id}")
    def get_tax_basis(
        book_id: str,
        review_id: str,
        current=Depends(actor_dependency),
        basis=Depends(basis_service),
    ):
        return basis.get(current, book_id, review_id)

    @router.post("/books/{book_id}/tax-basis/{review_id}/review")
    def review_tax_basis(
        book_id: str,
        review_id: str,
        payload: TaxBasisReview,
        current=Depends(actor_dependency),
        basis=Depends(basis_service),
    ):
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
                failures.append(
                    {
                        "status": "failed",
                        "external_id": item.external_id,
                        "http_status": 422,
                        "error": "文件内容不是有效的 Base64",
                        "retryable": True,
                    }
                )
                continue
            entries.append({**item.model_dump(exclude={"content_base64"}), "data": raw})
        result = sources.import_batch(current, book_id, entries)
        result["items"] = failures + result["items"]
        result["failed"] += len(failures)
        return result

    @router.get("/books/{book_id}/sources/{source_id}/original")
    def download_source_original(
        book_id: str,
        source_id: str,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        filename, content = sources.download_original(current, book_id, source_id)
        return Response(
            content,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(filename, safe=""),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.post("/books/{book_id}/sources/{source_id}/originals", status_code=201)
    def attach_source_original(
        book_id: str,
        source_id: str,
        payload: TaxSourceOriginalAttach,
        request: Request,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        return sources.attach_original(
            request.app.state.uploads, current, book_id, source_id, payload.upload_id
        )

    @router.get("/books/{book_id}/sources/{source_id}/originals/{attachment_id}")
    def download_source_original_file(
        book_id: str,
        source_id: str,
        attachment_id: str,
        request: Request,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        metadata = sources.original_for(current, book_id, source_id, attachment_id)
        data = request.app.state.attachment_store.read(metadata)
        return Response(
            data,
            media_type=metadata["media_type"],
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''"
                + quote(metadata["filename"], safe=""),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/books/{book_id}/sources/{source_id}/rows/{row_number}/receipt")
    def source_row_receipt(
        book_id: str,
        source_id: str,
        row_number: int,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        return sources.row_receipt(current, book_id, source_id, row_number)

    @router.patch("/books/{book_id}/sources/{source_id}/rows/{row_number}")
    def review_source_row(
        book_id: str,
        source_id: str,
        row_number: int,
        payload: TaxSourceRowWrite,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        return sources.review_row(
            current, book_id, source_id, row_number, status=payload.status, reason=payload.reason
        )

    @router.post("/books/{book_id}/sources/{source_id}/contributions", status_code=201)
    def record_source_contributions(
        book_id: str,
        source_id: str,
        payload: TaxSourceContributionWrite,
        current=Depends(actor_dependency),
        sources=Depends(source_service),
    ):
        return sources.record_contributions(
            current,
            book_id,
            source_id,
            output_kind=payload.output_kind,
            output_id=payload.output_id,
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
            current,
            book_id,
            output_kind=output_kind,
            output_id=output_id,
            output_version=output_version,
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
        return sources.list_contributions(
            current,
            book_id,
            output_kind=output_kind,
            output_id=output_id,
            output_version=output_version,
        )

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
        return fees.generate(
            current,
            book_id,
            period=payload.period,
            source_file_ids=payload.source_file_ids,
            policy=payload.policy.model_dump(),
        )

    @router.get("/books/{book_id}/fee-batches")
    def list_fee_batches(
        book_id: str, current=Depends(actor_dependency), fees=Depends(fee_service)
    ):
        return fees.list_batches(current, book_id)

    @router.get("/books/{book_id}/fee-receivables")
    def list_fee_receivables(
        book_id: str,
        period: str | None = Query(default=None),
        current=Depends(actor_dependency),
        fees=Depends(fee_service),
    ):
        return fees.list_receivables(current, book_id, period)

    @router.post("/books/{book_id}/fee-receivables/{receivable_id}/allocations")
    def allocate_fee_receivable(
        book_id: str,
        receivable_id: str,
        payload: FeeAllocationWrite,
        current=Depends(actor_dependency),
        fees=Depends(fee_service),
    ):
        return fees.allocate_receivable(
            current,
            book_id,
            receivable_id,
            bank_source_id=payload.bank_source_id,
            amount_cents=payload.amount_cents,
            idempotency_key=payload.idempotency_key,
            payer_name=payload.payer_name,
            collection_basis=payload.collection_basis,
            bank_statement_line_id=payload.bank_statement_line_id,
        )

    @router.get("/books/{book_id}/fee-bank-sources")
    def list_fee_bank_sources(
        book_id: str,
        period: str | None = Query(default=None),
        direction: str = Query(default="income", pattern="^(income|expense)$"),
        current=Depends(actor_dependency),
        fees=Depends(fee_service),
    ):
        return fees.list_bank_sources(current, book_id, period, direction)

    def chain_view(request: Request) -> FeeChainView:
        return request.app.state.fee_chain

    @router.get("/books/{book_id}/fee-chain/{batch_id}")
    def fee_chain(
        book_id: str,
        batch_id: str,
        current=Depends(actor_dependency),
        chains: FeeChainView = Depends(chain_view),
    ):
        """只读：规费批次 → 应收 → 银行核销 → 正式凭证 → 报表快照。"""
        return chains.chain(current, book_id, batch_id)

    @router.post("/books/{book_id}/fee-receivables/{receivable_id}/refunds")
    def refund_fee_receivable(
        book_id: str,
        receivable_id: str,
        payload: FeeRefundWrite,
        current=Depends(actor_dependency),
        fees=Depends(fee_service),
    ):
        return fees.refund_receivable(
            current,
            book_id,
            receivable_id,
            bank_source_id=payload.bank_source_id,
            amount_cents=payload.amount_cents,
            reason=payload.reason,
            idempotency_key=payload.idempotency_key,
        )

    @router.get("/books/{book_id}/fee-refunds")
    def list_fee_refunds(
        book_id: str,
        period: str | None = Query(default=None),
        current=Depends(actor_dependency),
        fees=Depends(fee_service),
    ):
        return fees.list_refunds(current, book_id, period)

    @router.post("/books/{book_id}/fee-allocations/{allocation_id}/reverse")
    def reverse_fee_allocation(
        book_id: str,
        allocation_id: str,
        payload: FeeAllocationReverse,
        current=Depends(actor_dependency),
        fees=Depends(fee_service),
    ):
        return fees.reverse_allocation(current, book_id, allocation_id, payload.reason)

    @router.get("/books/{book_id}/fee-allocations")
    def list_fee_allocations(
        book_id: str,
        period: str | None = Query(default=None),
        current=Depends(actor_dependency),
        fees=Depends(fee_service),
    ):
        return fees.list_allocations(current, book_id, period)

    @router.get("/books/{book_id}/fee-batches/{batch_id}")
    def get_fee_batch(
        book_id: str, batch_id: str, current=Depends(actor_dependency), fees=Depends(fee_service)
    ):
        return fees.get(current, book_id, batch_id)

    @router.post("/books/{book_id}/fee-batches/{batch_id}/review")
    def review_fee_batch(
        book_id: str,
        batch_id: str,
        payload: TaxFeeReviewWrite,
        current=Depends(actor_dependency),
        fees=Depends(fee_service),
    ):
        return fees.review(
            current,
            book_id,
            batch_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
        )

    @router.post("/books/{book_id}/fee-batches/{batch_id}/validate")
    def validate_fee_batch(
        book_id: str, batch_id: str, current=Depends(actor_dependency), fees=Depends(fee_service)
    ):
        return fees.validate(current, book_id, batch_id)

    @router.post("/books/{book_id}/fee-batches/{batch_id}/invalidate")
    def invalidate_fee_batch(
        book_id: str,
        batch_id: str,
        payload: TaxFeeInvalidateWrite,
        current=Depends(actor_dependency),
        fees=Depends(fee_service),
    ):
        return fees.invalidate(
            current,
            book_id,
            batch_id,
            expected_version=payload.expected_version,
            reason=payload.reason,
        )

    @router.get("/books/{book_id}/repair-packages")
    def list_repair_packages(
        book_id: str, current=Depends(actor_dependency), packages=Depends(package_service)
    ):
        return packages.list_contracts(current, book_id)

    @router.post("/books/{book_id}/repair-packages/drafts", status_code=201)
    def create_repair_package(
        book_id: str,
        payload: PackageContractDraft,
        current=Depends(actor_dependency),
        packages=Depends(package_service),
    ):
        return packages.create_draft(current, book_id, payload)

    @router.post("/books/{book_id}/repair-packages/{contract_id}/confirm")
    def confirm_repair_package(
        book_id: str,
        contract_id: str,
        payload: PackageVersion,
        current=Depends(actor_dependency),
        packages=Depends(package_service),
    ):
        return packages.confirm(current, book_id, contract_id, payload.expected_version)

    @router.post("/books/{book_id}/repair-packages/{contract_id}/activate")
    def activate_repair_package(
        book_id: str,
        contract_id: str,
        payload: PackageVersion,
        current=Depends(actor_dependency),
        packages=Depends(package_service),
    ):
        return packages.activate(current, book_id, contract_id, payload.expected_version)

    @router.post("/books/{book_id}/repair-packages/{contract_id}/consumptions", status_code=201)
    def consume_repair_package(
        book_id: str,
        contract_id: str,
        payload: PackageConsume,
        current=Depends(actor_dependency),
        packages=Depends(package_service),
    ):
        return packages.consume(current, book_id, contract_id, payload)

    @router.post(
        "/books/{book_id}/repair-packages/consumptions/{consume_id}/releases", status_code=201
    )
    def release_repair_package(
        book_id: str,
        consume_id: str,
        payload: PackageRelease,
        current=Depends(actor_dependency),
        packages=Depends(package_service),
    ):
        return packages.release(current, book_id, consume_id, payload)

    @router.get("/books/{book_id}/repair-packages/{contract_id}/summary")
    def repair_package_summary(
        book_id: str,
        contract_id: str,
        current=Depends(actor_dependency),
        packages=Depends(package_service),
    ):
        return packages.summary(current, book_id, contract_id)

    @router.post("/books/{book_id}/repair-rates", status_code=201)
    def add_repair_rate(
        book_id: str,
        payload: RepairRateWrite,
        current=Depends(actor_dependency),
        rates=Depends(rate_service),
    ):
        return rates.add(current, book_id, payload)

    @router.post("/books/{book_id}/repair-rates/drafts", status_code=201)
    def create_repair_rate_draft(
        book_id: str,
        payload: RepairRateDraft,
        current=Depends(actor_dependency),
        rates=Depends(rate_service),
    ):
        return rates.create_draft(current, book_id, payload)

    @router.post("/books/{book_id}/repair-rates/{card_id}/confirm")
    def confirm_repair_rate(
        book_id: str,
        card_id: str,
        payload: RepairRateVersion,
        current=Depends(actor_dependency),
        rates=Depends(rate_service),
    ):
        return rates.confirm(current, book_id, card_id, payload.expected_version)

    @router.post("/books/{book_id}/repair-rates/{card_id}/activate")
    def activate_repair_rate(
        book_id: str,
        card_id: str,
        payload: RepairRateVersion,
        current=Depends(actor_dependency),
        rates=Depends(rate_service),
    ):
        return rates.activate(current, book_id, card_id, payload.expected_version)

    @router.post("/books/{book_id}/repair-rates/{card_id}/renew", status_code=201)
    def renew_repair_rate(
        book_id: str,
        card_id: str,
        payload: RepairRateDraft,
        current=Depends(actor_dependency),
        rates=Depends(rate_service),
    ):
        return rates.renew(current, book_id, card_id, payload)

    @router.post("/books/{book_id}/repair-rates/{card_id}/deactivate")
    def deactivate_repair_rate(
        book_id: str,
        card_id: str,
        payload: RepairRateDeactivate,
        current=Depends(actor_dependency),
        rates=Depends(rate_service),
    ):
        return rates.deactivate(current, book_id, card_id, payload)

    @router.get("/books/{book_id}/repair-rates/logical/{logical_id}/history")
    def repair_rate_history(
        book_id: str,
        logical_id: str,
        current=Depends(actor_dependency),
        rates=Depends(rate_service),
    ):
        return rates.history(current, book_id, logical_id)

    @router.get("/books/{book_id}/repair-rates/{card_id}/impact")
    def repair_rate_impact(
        book_id: str,
        card_id: str,
        current=Depends(actor_dependency),
        rates=Depends(rate_service),
    ):
        return rates.impact(current, book_id, card_id)

    @router.get("/books/{book_id}/repair-rates")
    def list_repair_rates(
        book_id: str, current=Depends(actor_dependency), rates=Depends(rate_service)
    ):
        return rates.list(current, book_id)

    @router.get("/books/{book_id}/repair-rates/resolve")
    def resolve_repair_rate(
        book_id: str,
        vehicle_type: str,
        period: str,
        current=Depends(actor_dependency),
        rates=Depends(rate_service),
    ):
        return rates.resolve(current, book_id, vehicle_type, period)

    @router.post("/books/{book_id}/cost-sources", status_code=201)
    def add_cost_source(
        book_id: str,
        payload: TaxCostSourceWrite,
        current=Depends(actor_dependency),
        costs=Depends(cost_service),
    ):
        return costs.add_source(current, book_id, payload)

    @router.get("/books/{book_id}/cost-sources")
    def list_cost_sources(
        book_id: str,
        kind: Literal["repair", "insurance", "oa"] | None = None,
        period: str | None = None,
        current=Depends(actor_dependency),
        costs=Depends(cost_service),
    ):
        return costs.list_sources(current, book_id, kind=kind, period=period)

    @router.post("/books/{book_id}/cost-batches", status_code=201)
    def generate_cost_batch(
        book_id: str,
        payload: TaxCostGenerate,
        current=Depends(actor_dependency),
        costs=Depends(cost_service),
    ):
        return costs.generate(current, book_id, payload)

    @router.get("/books/{book_id}/cost-batches")
    def list_cost_batches(
        book_id: str,
        kind: Literal["repair", "insurance", "oa"] | None = None,
        current=Depends(actor_dependency),
        costs=Depends(cost_service),
    ):
        return costs.list_batches(current, book_id, kind=kind)

    @router.get("/books/{book_id}/cost-batches/{batch_id}")
    def get_cost_batch(
        book_id: str, batch_id: str, current=Depends(actor_dependency), costs=Depends(cost_service)
    ):
        return costs.get_batch(current, book_id, batch_id)

    @router.post("/books/{book_id}/cost-batches/{batch_id}/validate")
    def validate_cost_batch(
        book_id: str, batch_id: str, current=Depends(actor_dependency), costs=Depends(cost_service)
    ):
        return costs.validate(current, book_id, batch_id)

    @router.post("/books/{book_id}/cost-batches/{batch_id}/review")
    def review_cost_batch(
        book_id: str,
        batch_id: str,
        payload: TaxCostReview,
        current=Depends(actor_dependency),
        costs=Depends(cost_service),
    ):
        return costs.review(current, book_id, batch_id, payload)

    @router.post("/books/{book_id}/cost-batches/{batch_id}/invalidate")
    def invalidate_cost_batch(
        book_id: str,
        batch_id: str,
        payload: TaxCostInvalidate,
        current=Depends(actor_dependency),
        costs=Depends(cost_service),
    ):
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

    @router.get("/books/{book_id}/fee-variances")
    def list_fee_variances(
        book_id: str,
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        service=Depends(variances),
    ):
        return service.list(current, book_id, period)

    @router.get("/books/{book_id}/fee-variance-reasons")
    def list_fee_variance_reasons(
        book_id: str,
        current=Depends(actor_dependency),
        service=Depends(variances),
    ):
        return service.reasons(current, book_id)

    @router.get("/books/{book_id}/fee-variance-assignees")
    def list_fee_variance_assignees(
        book_id: str,
        current=Depends(actor_dependency),
        service=Depends(variances),
    ):
        return {"items": service.assignees(current, book_id)}

    @router.post("/books/{book_id}/fee-variances", status_code=201)
    def record_fee_variance(
        book_id: str,
        payload: FeeVarianceRecord,
        current=Depends(actor_dependency),
        service=Depends(variances),
    ):
        return service.record(
            current,
            book_id,
            **payload.model_dump(),
        )

    @router.post("/fee-variances/{variance_id}/start")
    def start_fee_variance(
        variance_id: str,
        current=Depends(actor_dependency),
        service=Depends(variances),
    ):
        return service.start(current, variance_id)

    @router.post("/fee-variances/{variance_id}/assign")
    def assign_fee_variance(
        variance_id: str,
        payload: FeeVarianceAssign,
        current=Depends(actor_dependency),
        service=Depends(variances),
    ):
        return service.assign(current, variance_id, payload.assignee_id, payload.reason)

    @router.post("/fee-variances/{variance_id}/handle")
    def handle_fee_variance(
        variance_id: str,
        payload: FeeVarianceHandle,
        current=Depends(actor_dependency),
        service=Depends(variances),
    ):
        return service.handle(current, variance_id, **payload.model_dump())

    def periods(request: Request):
        from .periods import TaxPeriodService

        tax = request.app.state.tax
        return TaxPeriodService(tax.db, tax.access)

    class PeriodRunIn(BaseModel):
        model_config = ConfigDict(extra="forbid")
        kind: str = Field(min_length=1, max_length=40)
        period: str = Field(min_length=7, max_length=7)
        payload: dict
        source_digest: str = Field(default="", max_length=128)

    class PeriodReviewIn(BaseModel):
        model_config = ConfigDict(extra="forbid")
        note: str = Field(min_length=1, max_length=500)
        expected_version: int

    class FourTaxIn(BaseModel):
        model_config = ConfigDict(extra="forbid")
        book_id: str = Field(min_length=1, max_length=100)
        period: str = Field(min_length=7, max_length=7)
        surtax_items: list[dict] = Field(default_factory=list)
        cumulative_profit_cents: int | None = None
        loss_carryforward_cents: int = 0
        prepaid_cents: int = 0
        cit_rate_bp: int = 2_500
        halved: bool = False
        stamp_contracts: list[dict] | None = None
        stamp_rates: dict[str, int] | None = None
        stamp_zero_confirmed: bool = False

    class AccrualManualRequestIn(FourTaxIn):
        """Same four-tax request plus the two facts a voucher needs.

        ``voucher_date`` is optional; it defaults to the last day of the period.
        Validated here so a bad date is a 422 from the request model, not a
        Pydantic ``ValidationError`` escaping from the service layer as a 500.
        """

        voucher_date: str | None = Field(default=None, min_length=10, max_length=10)
        description: str | None = Field(default=None, max_length=500)
        reason: str | None = Field(default=None, max_length=1000)

        @field_validator("voucher_date")
        @classmethod
        def valid_optional_date(cls, value):
            if value is not None:
                date.fromisoformat(value)
            return value

    @router.get("/period-runs")
    def list_period_runs(
        book_id: str = Query(min_length=1, max_length=100),
        period: str | None = Query(default=None, max_length=7),
        current=Depends(actor_dependency),
        runs=Depends(periods),
    ):
        return runs.list(current, book_id, period=period)

    @router.post("/period-runs")
    def record_period_run(
        payload: PeriodRunIn,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        runs=Depends(periods),
    ):
        return runs.record(
            current,
            book_id,
            period=payload.period,
            kind=payload.kind,
            payload=payload.payload,
            source_digest=payload.source_digest,
        )

    @router.post("/period-runs/{run_id}/review")
    def review_period_run(
        run_id: str,
        payload: PeriodReviewIn,
        book_id: str = Query(min_length=1, max_length=100),
        current=Depends(actor_dependency),
        runs=Depends(periods),
    ):
        return runs.review(
            current, book_id, run_id, note=payload.note, expected_version=payload.expected_version
        )

    @router.post("/accrual-preview")
    def accrual_preview(
        payload: AccrualManualRequestIn,
        current=Depends(actor_dependency),
        tax=Depends(service),
        posting=Depends(poster),
    ):
        """Preview the accrual entries for a period.

        Read-only on purpose: this endpoint never writes a voucher. It shares the
        exact package composition with ``/accrual-manual-request`` so the preview
        cannot show amounts the request would refuse.
        """
        from ..accounting.domain import AccountingError
        from . import accrual

        with posting.db.transaction(actor=current) as conn:
            book, _ = posting._book(conn, current, payload.book_id)
        if payload.period != book["period"]:
            raise AccountingError(
                f"计提预览只能针对账套当前会计期间（当前 {book['period']}，请求 {payload.period}）",
                409,
            )
        package, basis = four_tax_package_for(posting, tax, current, payload, book)
        preview = accrual.accrual_lines(package)
        return {
            **preview,
            "surtax_basis": basis,
            "posted": False,
            "note": "预览仅供参考：本接口不写账，也不会生成草稿凭证。",
        }

    @router.post("/accrual-manual-request", status_code=201)
    def accrual_manual_request(
        payload: AccrualManualRequestIn,
        current=Depends(actor_dependency),
        tax=Depends(service),
        posting=Depends(poster),
        reviews=Depends(manual_reviews),
    ):
        """Turn the reviewed four-tax accrual into a manual-voucher request.

        This does not open a second posting path: it builds the voucher the
        existing contract already accepts and hands it to
        ``ManualReviewService.prepare``, so independent review, the idempotent
        ``command_key`` and the month-end ``unposted_manual`` gate all apply.
        """
        from ..accounting.domain import AccountingError
        from ..posting.models import ManualVoucher
        from . import accrual, accrual_posting

        # Account identities are resolved *as of the book's period*, so a posting
        # can never land on an account version that was rewritten later.
        with posting.db.transaction(actor=current) as conn:
            book, ledger_id = posting._book(conn, current, payload.book_id)
            accounts = posting._accounts(conn, ledger_id, book["period"])
        if payload.period != book["period"]:
            raise AccountingError(
                f"计提入账只能针对账套当前会计期间（当前 {book['period']}，请求 {payload.period}）",
                409,
            )
        by_code = {
            meta["code"]: {"id": account_id, "version": int(meta["version"])}
            for account_id, meta in accounts.items()
        }
        package, surtax_basis = four_tax_package_for(posting, tax, current, payload, book)
        surtax_cents = int(package["surtax"]["total_cents"])
        package = {**package, "source": accrual_posting.normalize_source(package.get("source"))}
        # No ``chart=`` here: it would demand every default rail code to exist even
        # for taxes that are zero this period (a client without a stamp-duty
        # account could not accrue surtax). ``manual_voucher`` resolves each code
        # it is actually about to post and names the one that is missing.
        lines = accrual.accrual_lines(package)
        voucher_date = payload.voucher_date or _period_end(book["period"])
        body = accrual_posting.manual_voucher(
            package,
            lines["entries"],
            by_code,
            book_id=book["id"],
            voucher_date=voucher_date,
            description=payload.description or "",
            reason=payload.reason or "",
        )
        prepared = reviews.prepare(current, ManualVoucher(**body))
        return {
            "request": prepared["request"],
            "voucher": {
                "command_key": body["command_key"],
                "voucher_date": body["voucher_date"],
                "line_count": len(body["lines"]),
                "debit_cents": lines["debit_cents"],
                "credit_cents": lines["credit_cents"],
            },
            "refund_note": lines["refund_note"],
            "stamp_duty_status": lines["stamp_duty_status"],
            "stamp_duty_notice": lines["stamp_duty_notice"],
            "notices": lines["notices"],
            "surtax_basis": surtax_basis,
            "surtax_cents": surtax_cents,
            "posted": False,
            "note": "已生成待复核的手工凭证申请：须由另一名有复核权的人批准后才能过账，"
            "本接口不会直接记账。",
        }

    @router.post("/four-tax")
    def four_tax_package(
        payload: FourTaxIn,
        current=Depends(actor_dependency),
        tax=Depends(service),
    ):
        from . import four_tax

        return four_tax.package(
            tax,
            current,
            payload.book_id,
            payload.period,
            surtax_items=payload.surtax_items,
            cumulative_profit_cents=payload.cumulative_profit_cents,
            loss_carryforward_cents=payload.loss_carryforward_cents,
            prepaid_cents=payload.prepaid_cents,
            cit_rate_bp=payload.cit_rate_bp,
            halved=payload.halved,
            stamp_contracts=payload.stamp_contracts,
            stamp_rates=payload.stamp_rates,
            stamp_zero_confirmed=payload.stamp_zero_confirmed,
        )

    @router.post("/fee-variances/{variance_id}/reverse")
    def reverse_fee_variance(
        variance_id: str,
        payload: FeeVarianceReverse,
        current=Depends(actor_dependency),
        service=Depends(variances),
    ):
        return service.reverse(current, variance_id, reason=payload.reason)

    return router
