"""Latest tax/delivery evidence for the explicit isolated recovery drill.

All business state is created through production services. Notifications never
call a transport: claim/begin and an ambiguous settlement model process loss.
The returned lease token is private test state and must not enter public reports.
"""

import hashlib
import io
import json
import os
import time
from unittest.mock import patch
from uuid import uuid4
from zipfile import ZipFile

from app.accounting.directory import DirectoryService
from app.accounting.models import BookCreate, BookMemberGrant, CompanyCreate, CompanyMemberGrant
from app.accounting.service import AccountingService
from app.b04_close.models import CloseCommand, ClosePreflight
from app.b04_close.service import CloseService
from app.business.service import BusinessService
from app.delivery.followup_models import DeliveryGrantRequest, DeliveryTaskRequest
from app.delivery.followups import DeliveryFollowupService, LocalSinkChannel
from app.delivery.monthly_models import MonthlyPackageAcknowledge, MonthlyPackageCreate
from app.delivery.monthly_package import MonthlyPackageService
from app.invoices.service import InvoiceImportService
from app.ledgers.models import ManagerGrant
from app.ledgers.service import LedgersService
from app.opening.models import (
    AccountCreate,
    OpeningCommand,
    OpeningImport,
    OpeningLineInput,
    OpeningPackageCreate,
    PolicyCreate,
)
from app.opening.service import OpeningService
from app.posting.models import OpeningBind
from app.posting.service import PostingService
from app.reports.models import ReportCommand
from app.reports.service import ReportService
from app.service_plans.models import PlanSave
from app.service_plans.service import ServicePlansService
from app.subledger.models import ControlApplicability
from app.subledger.service import SubledgerService
from app.tax.workpaper import TaxWorkpaperService
from app.uploads import UploadCreate, UploadIntentService


from permission_recovery_business import TABLE_FIELDS

PERIOD = "2026-10"
BUSINESS_TABLES = (
    "tax_profiles",
    "tax_workpapers",
    "monthly_delivery_packages",
    "monthly_delivery_grants",
    "monthly_delivery_acknowledgements",
    "monthly_delivery_opens",
    "delivery_notifications",
    "notification_investigations",
    "tax_filings",
    "tax_filing_attachments",
    "delivery_provider_events",
    "handoff_tasks",
    *TABLE_FIELDS,
)


def require_latest_business_inventory(inventory):
    """Two equal empty inventories are not proof of recovering these domains."""
    for table in BUSINESS_TABLES:
        assert inventory.get("tables", {}).get(table), f"P01 recovery lacks populated {table}"


def _zero_formal_opening(db, owner, reviewer, book):
    """A real zero opening, independently reviewed before it is bound."""
    ledger = LedgersService(db)
    ledger.grant_manager(
        owner, book["id"], ManagerGrant(user_id=reviewer.user_id, reason="本地独立恢复期初复核")
    )
    with db.transaction(actor=owner) as conn:
        ledger_id = conn.execute(
            "SELECT ledger_id FROM ledger_periods WHERE book_id=?", (book["id"],)
        ).fetchone()[0]
    opening = OpeningService(db)
    policy = opening.create_policy(
        owner,
        PolicyCreate(
            ledger_id=ledger_id,
            standard_kind="small_enterprise",
            taxpayer_kind="small_scale",
            effective_from="2026-01",
            effective_to="2026-12",
            reason="P01虚构零业务企业核算政策",
        ),
    )
    accounts = [
        opening.create_account(
            owner,
            AccountCreate(
                ledger_id=ledger_id,
                code=code,
                name=name,
                category=category,
                normal_balance=normal,
                effective_from="2026-01",
                effective_to="2026-12",
                reason="P01真实核算配置",
            ),
        )
        for code, name, category, normal in (
            ("1002", "银行存款", "asset", "debit"),
            ("2202", "应付账款", "liability", "credit"),
        )
    ]
    package = opening.create_package(
        owner,
        OpeningPackageCreate(
            ledger_id=ledger_id,
            target_period=PERIOD,
            policy_id=policy["id"],
        ),
    )
    version = opening.import_version(
        owner,
        package["package"]["id"],
        OpeningImport(
            idempotency_key="p01-opening-" + uuid4().hex,
            reason="虚构企业无期初业务，不编造当期流水",
            lines=[
                OpeningLineInput(
                    account_id=a["id"],
                    account_version=1,
                    year_opening="0.00",
                    ytd_debit="0.00",
                    ytd_credit="0.00",
                    opening="0.00",
                )
                for a in accounts
            ],
        ),
    )
    version_id = version["version"]["id"]
    opening.validate_version(owner, version_id)
    opening.submit_version(
        owner, version_id, OpeningCommand(expected_version=1, reason="提交零余额正式期初")
    )
    opening.approve_version(
        reviewer, version_id, OpeningCommand(expected_version=1, reason="独立确认无期初发生")
    )
    opening.freeze_version(
        reviewer, version_id, OpeningCommand(expected_version=1, reason="冻结已独立复核期初")
    )
    PostingService(db).bind_opening(
        owner, OpeningBind(book_id=book["id"], package_version_id=version_id, reason="绑定正式期初")
    )


def _zero_activity_close(db, owner, reviewer, book):
    """A real zero-activity client: no invented bank flows or bypassed close flags."""
    _zero_formal_opening(db, owner, reviewer, book)
    SubledgerService(db).set_control_applicability(
        owner,
        ControlApplicability(
            book_id=book["id"],
            status="not_applicable",
            effective_from=PERIOD,
            reason="合成零业务企业，无应收应付余额及发生",
        ),
    )
    reports = ReportService(db)
    report = reports.create(owner, book["id"], PERIOD)
    report_id = report["report"]["id"]
    reports.validate(owner, report_id)
    reports.review(
        reviewer, report_id, ReportCommand(expected_version=1, reason="独立复核零业务报表")
    )
    closing = CloseService(db)
    run = closing.preflight(
        owner,
        ClosePreflight(
            book_id=book["id"],
            period=PERIOD,
            report_id=report_id,
            idempotency_key="p01-close-" + uuid4().hex,
            reason="真实预检，无跳过银行或子账校验",
        ),
    )
    assert run["close"]["status"] == "preflight_passed", run["checks"]
    closing.review(
        reviewer, run["close"]["id"], CloseCommand(expected_version=1, reason="独立复核月结证据")
    )
    closing.lock(
        reviewer, run["close"]["id"], CloseCommand(expected_version=1, reason="锁定已复核账期")
    )
    reports.publish(reviewer, report_id, ReportCommand(expected_version=1, reason="发布已锁期报表"))
    return report_id


def seed_latest_recovery_business(
    db, worker_db, storage, *, owner, reviewer, customer, customer_email
):
    """Populate N02/N03/N04 via scoped services using already verified identities."""
    directory, accounting = DirectoryService(db), AccountingService(db)
    tax_number = "P01" + uuid4().hex[:15].upper()
    company = directory.create_company(
        owner, CompanyCreate(name="P01虚构恢复税务及交付企业", tax_id=tax_number)
    )
    directory.grant_company_member(
        owner, company["id"], CompanyMemberGrant(email=customer_email, role="finance")
    )
    tax_book = accounting.create_book(
        owner, BookCreate(name="P01开放税务底稿账套", period=PERIOD, company_id=company["id"])
    )
    delivery_book = accounting.create_book(
        owner, BookCreate(name="P01零业务交付账套", period=PERIOD, company_id=company["id"])
    )
    for book in (tax_book, delivery_book):
        directory.grant_book_member(
            owner, book["id"], reviewer.user_id, BookMemberGrant(role="reviewer")
        )
        directory.grant_book_member(
            owner, book["id"], customer.user_id, BookMemberGrant(role="viewer")
        )

    # Actual immutable CSV original, verified upload intent and import parser.
    original = (
        "发票种类,发票代码,发票号码,开票日期,销方名称,销方纳税人识别号,购方名称,购方纳税人识别号,金额,税额,价税合计,发票状态\n"
        f"数电票（增值税专用发票）,,000001,2026-10-05,P01虚构销方,{tax_number},虚构购买方,BUYER00000000000001,1000.00,30.00,1030.00,正常\n"
    ).encode("utf-8-sig")
    uploads = UploadIntentService(db, storage)
    upload = uploads.create(
        owner,
        UploadCreate(
            purpose="invoice_import",
            filename="P01虚构发票.csv",
            media_type="text/csv",
            size_bytes=len(original),
            sha256=hashlib.sha256(original).hexdigest(),
        ),
    )
    uploads.store_content(owner, upload["id"], original)
    imported = InvoiceImportService(db, storage, uploads).create(
        owner, tax_book["id"], upload["id"]
    )
    tax = TaxWorkpaperService(db)
    profile = tax.set_profile(
        owner,
        tax_book["id"],
        {
            "effective_from": "2026-01",
            "taxpayer_type": "small_scale",
            "vat_rate_bp": 300,
            "surtax_rate_bp": 1200,
            "filing_cycle": "monthly",
            "reason": "本地恢复合成税率，不作真实申报",
        },
    )
    _zero_formal_opening(db, owner, reviewer, tax_book)
    prior = tax.generate(owner, tax_book["id"], PERIOD)
    prior = tax.review(
        reviewer,
        tax_book["id"],
        prior["id"],
        "恢复前独立核对发票与正式账差异，不代表已申报",
        prior["version"],
    )
    prior_csv = tax.csv(
        reviewer, tax_book["id"], prior["id"], prior["version"], prior["source_digest"]
    )
    from app.tax import filings
    from app.handoff.service import HandoffService

    filing = filings.record(
        tax,
        reviewer,
        {
            "book_id": tax_book["id"],
            "period": PERIOD,
            "kind": "vat",
            "status": "filed",
            "external_ref": "P01-SYNTHETIC-" + uuid4().hex,
            "occurred_at": "2026-11-01",
            "amount_cents": 3000,
            "evidence_note": "虚构回执，只证明人工登记与原件恢复",
        },
    )
    filing_original = b"%PDF-1.4\nP01 synthetic filing receipt\n%%EOF"
    filing_upload = uploads.create(
        reviewer,
        UploadCreate(
            purpose="tax_filing_attachment",
            filename="P01-filing.pdf",
            media_type="application/pdf",
            size_bytes=len(filing_original),
            sha256=hashlib.sha256(filing_original).hexdigest(),
        ),
    )
    uploads.store_content(reviewer, filing_upload["id"], filing_original)
    filing_attachment = filings.attach(uploads, tax, reviewer, filing["id"], filing_upload["id"])
    paper = tax.generate(owner, tax_book["id"], PERIOD)
    assert paper["tax"] == "30.00" and paper["status"] == "draft"
    handoff = HandoffService(db).assign(
        owner,
        paper["id"],
        paper["version"],
        reviewer.user_id,
        "2026-11-10",
        "恢复后由原独立复核人继续办理，不额外授予权限",
    )

    report_id = _zero_activity_close(db, owner, reviewer, delivery_book)
    ServicePlansService(db, accounting, BusinessService(db)).save_plan(
        owner,
        delivery_book["id"],
        PlanSave(
            expected_version=0,
            contact_user_id=customer.user_id,
            due_on="2026-11-10",
            reason="明确恢复后签收主体",
        ),
    )
    packages = MonthlyPackageService(db)
    followups, dispatcher = DeliveryFollowupService(db), DeliveryFollowupService(worker_db)
    # Recipient administration is performed by the actual firm owner. The PG
    # reviewer-only membership projection currently cannot inspect other client
    # members; P01 must not widen RLS or claim that role is proven by this drill.
    grant = followups.configure(
        owner,
        delivery_book["id"],
        DeliveryGrantRequest(
            period=PERIOD, recipient_user_id=customer.user_id, reason="已确认独立客户收件人"
        ),
    )
    frozen, notifications, sending = [], {}, None
    for version in range(1, 5):
        package = packages.create(
            reviewer,
            delivery_book["id"],
            MonthlyPackageCreate(
                idempotency_key=f"p01-package-{uuid4().hex}",
                report_id=report_id,
                expected_report_version=1,
                reason=f"虚构恢复证据包版本{version}",
            ),
        )
        assert package["version"] == version
        frozen.append(package)
        if version == 1:
            packages.open(customer, delivery_book["id"], package["id"])
            packages.acknowledge(
                customer,
                delivery_book["id"],
                package["id"],
                MonthlyPackageAcknowledge(
                    manifest_sha256=package["manifest_sha256"], reason="恢复前客户收到准确版本"
                ),
            )
            continue
        queued = followups.queue(
            owner,
            delivery_book["id"],
            DeliveryTaskRequest(
                period=PERIOD, kind="signature_reminder", reason="本地恢复通知状态样本，不真实发送"
            ),
        )
        if version == 4:
            notifications["pending"] = queued["notification_id"]
            continue
        claimed = dispatcher.claim("p01-before-restore", lease_seconds=300)
        assert claimed["id"] == queued["notification_id"]
        started = dispatcher.begin(claimed["id"], claimed["lease_token"])
        assert started and started["state"] == "sending"
        if version == 2:
            assert dispatcher.finish(
                started["id"], started["lease_token"], "unknown", "", "synthetic_response_lost"
            )
            notifications["unknown"] = started["id"]
        else:
            sending = {"id": started["id"], "lease_token": started["lease_token"]}
            notifications["sending"] = started["id"]
    assert sending
    from app.delivery.notification_investigations import (
        NotificationInvestigations,
        InvestigationCreate,
    )

    with db.transaction(actor=owner) as conn:
        source = conn.execute(
            "SELECT * FROM delivery_notifications WHERE id=?", (notifications["unknown"],)
        ).fetchone()
    investigation = NotificationInvestigations(db).create(
        owner,
        delivery_book["id"],
        source["id"],
        InvestigationCreate(
            expected_revision=0,
            expected_attempts=source["attempts"],
            expected_package_version=source["package_version"],
            expected_manifest_sha256=source["manifest_sha256"],
            expected_recipient_user_id=source["recipient_user_id"],
            conclusion="unresolved",
            evidence_ref="synthetic recovery ticket 202",
            reason="恢复前未知外发保留人工核查",
            idempotency_key="recovery-investigation-" + uuid4().hex,
        ),
    )
    from app.delivery.receipts import ProviderReceiptService, sign

    event_id = "p01-provider-" + uuid4().hex
    receipt_raw = json.dumps(
        {
            "notification_id": notifications["unknown"],
            "provider_event_id": event_id,
            "event_type": "accepted",
            "occurred_at": "2026-11-01T00:00:00+00:00",
        }
    ).encode()
    timestamp = str(int(time.time()))
    with patch.dict(
        os.environ, DELIVERY_CALLBACK_SECRET="p01-synthetic-secret-not-production-012345"
    ):
        receipt = ProviderReceiptService(worker_db).receive(
            receipt_raw,
            timestamp,
            sign(receipt_raw, timestamp),
            event_id,
        )
    return {
        "tax_filing": filing,
        "filing_attachment": filing_attachment,
        "filing_original": filing_original,
        "handoff": handoff,
        "provider_receipt": receipt["event"],
        "notification_investigation": investigation,
        "company_id": company["id"],
        "tax_book": tax_book,
        "delivery_book": delivery_book,
        "tax_profile": profile,
        "tax_prior": prior,
        "tax_prior_csv_sha256": hashlib.sha256(prior_csv.encode()).hexdigest(),
        "tax_paper": paper,
        "invoice_batch": imported,
        "invoice_original": original,
        "invoice_sha256": hashlib.sha256(original).hexdigest(),
        "packages": frozen,
        "grant": grant,
        "notifications": notifications,
        "sending_private": sending,
        "customer_id": customer.user_id,
    }


def assert_notifications_fenced(worker_db, evidence):
    """Calling a restored worker cannot send or settle an old generation."""
    service = DeliveryFollowupService(worker_db)
    sending = evidence["sending_private"]
    assert (
        service.finish(
            sending["id"], sending["lease_token"], "sink_written", "old-generation-receipt", ""
        )
        is False
    )

    class CountingChannel(LocalSinkChannel):
        calls = 0

        def deliver(self, payload):
            self.calls += 1
            return super().deliver(payload)

    channel = CountingChannel()
    service.recover()
    first, second = service.dispatch(channel), service.dispatch(channel)
    assert channel.calls == 0 and first["sent"] == second["sent"] == 0
    return {"channel_calls": channel.calls, "old_lease_settlement": False}


def _response(client, method, path, headers, body=None, expected=200):
    response = client.request(method, path, headers=headers, json=body)
    assert response.status_code == expected, (
        f"P01 {method} {path}: {response.status_code} {response.text[:400]}"
    )
    return response


def verify_latest_business_http(client, headers, evidence, *, wrong_book_id):
    """Fresh signed sessions exercise original IO, independent review and exact acknowledgement."""
    tax_book, delivery_book = evidence["tax_book"]["id"], evidence["delivery_book"]["id"]
    filing = evidence["tax_filing"]
    rows = _response(
        client, "GET", f"/api/tax/filings?book_id={tax_book}", headers["reviewer"]
    ).json()["filings"]
    restored = next(row for row in rows if row["id"] == filing["id"])
    for key in ("workpaper_id", "workpaper_version", "amount_cents", "external_ref", "entry_kind"):
        assert restored[key] == filing[key]
    attachment = restored["attachment"]
    original_path = f"/api/tax/filings/{filing['id']}/attachments/{attachment['id']}"
    original = _response(client, "GET", original_path, headers["reviewer"]).content
    assert original == evidence["filing_original"]
    assert hashlib.sha256(original).hexdigest() == attachment["sha256"]
    for role in ("outsider", "employee"):
        assert client.get(original_path, headers=headers[role]).status_code in (403, 404)
    queue = _response(client, "GET", "/api/handoff", headers["owner"]).json()
    assigned = next(
        item for item in queue["items"] if item["source"]["id"] == evidence["tax_paper"]["id"]
    )
    assert assigned["assignee_id"] == evidence["handoff"]["assignee_id"]
    assert assigned["due_on"] == evidence["handoff"]["due_on"]
    assert assigned["source"]["fingerprint"] == evidence["tax_paper"]["source_digest"]
    receipt_path = f"/api/books/{delivery_book}/delivery/notifications/{evidence['provider_receipt']['notification_id']}/receipts"
    timeline = _response(client, "GET", receipt_path, headers["owner"]).json()
    assert any(
        event["provider_event_id"] == evidence["provider_receipt"]["provider_event_id"]
        for event in timeline["events"]
    )
    assert timeline["state"] == "unknown" and not timeline["signed"]
    assert client.get(receipt_path, headers=headers["outsider"]).status_code in (403, 404)
    investigation = evidence["notification_investigation"]
    history_path = f"/api/books/{delivery_book}/delivery/notifications/{investigation['notification_id']}/investigations"
    history = _response(client, "GET", history_path, headers["owner"]).json()
    assert history["items"] == [investigation]
    assert client.get(history_path, headers=headers["outsider"]).status_code in (403, 404)
    # A customer cannot read internal investigation evidence or turn it into a signature.
    assert client.get(history_path, headers=headers["customer"]).status_code in (403, 404)

    paper = evidence["tax_paper"]
    path = f"/api/tax/workpapers/{paper['id']}"
    returned = _response(client, "GET", path + f"?book_id={tax_book}", headers["reviewer"]).json()
    for key in ("id", "version", "source_digest", "source_snapshot", "profile_id", "tax", "filing"):
        assert returned[key] == paper[key], key
    assert returned["status"] == "draft"
    prior = evidence["tax_prior"]
    historic = _response(
        client,
        "GET",
        f"/api/tax/workpapers/{prior['id']}?book_id={tax_book}",
        headers["reviewer"],
    ).json()
    for key in ("id", "version", "status", "source_digest", "source_snapshot", "reviewed_by"):
        assert historic[key] == prior[key], key
    _response(
        client,
        "GET",
        f"/api/tax/workpapers/{prior['id']}/csv?book_id={tax_book}"
        f"&expected_version={prior['version']}&source_digest={prior['source_digest']}",
        headers["reviewer"],
        expected=409,
    )
    csv_path = (
        path
        + f"/csv?book_id={tax_book}&expected_version={paper['version']}&source_digest={paper['source_digest']}"
    )
    _response(client, "GET", csv_path, headers["reviewer"], expected=409)
    _response(
        client,
        "POST",
        path + "/review",
        headers["owner"],
        {"book_id": tax_book, "expected_version": paper["version"], "reason": "制单人不得自行复核"},
        expected=403,
    )
    reviewed = _response(
        client,
        "POST",
        path + "/review",
        headers["reviewer"],
        {
            "book_id": tax_book,
            "expected_version": paper["version"],
            "reason": "独立核对恢复原件及正式账差异",
        },
    ).json()
    assert reviewed["status"] == "reviewed"
    exported = _response(client, "GET", csv_path, headers["reviewer"])
    assert "not_filed" in exported.text and "30.00" in exported.text
    _response(
        client,
        "GET",
        csv_path.replace(paper["source_digest"], "a" * 64),
        headers["reviewer"],
        expected=409,
    )
    for route in (path + f"?book_id={wrong_book_id}", path + f"?book_id={tax_book}"):
        denied = client.get(route, headers=headers["outsider"])
        assert denied.status_code in (403, 404)

    invoice = evidence["invoice_batch"]
    invoice_id = invoice.get("id") or invoice.get("batch", {}).get("id")
    original = _response(
        client,
        "GET",
        f"/api/books/{tax_book}/invoice-imports/{invoice_id}/original",
        headers["owner"],
    )
    assert original.content == evidence["invoice_original"]
    assert hashlib.sha256(original.content).hexdigest() == evidence["invoice_sha256"]

    first, last = evidence["packages"][0], evidence["packages"][-1]
    prefix = f"/api/books/{delivery_book}/delivery/monthly-package/"
    prior = _response(client, "GET", prefix + first["id"], headers["customer"]).json()["package"]
    assert prior["customer_receipt_status"] == "acknowledged"
    latest = _response(client, "GET", prefix + last["id"], headers["customer"]).json()["package"]
    assert latest["customer_receipt_status"] == "not_recorded"
    assert (latest["version"], latest["manifest_sha256"]) == (
        last["version"],
        last["manifest_sha256"],
    )
    for user in ("employee", "outsider"):
        denied = client.get(prefix + last["id"] + "/download", headers=headers[user])
        assert denied.status_code in (403, 404)
    wrong = client.get(
        f"/api/books/{wrong_book_id}/delivery/monthly-package/{last['id']}",
        headers=headers["owner"],
    )
    assert wrong.status_code == 404
    blob = _response(client, "GET", prefix + last["id"] + "/download", headers["customer"]).content
    with ZipFile(io.BytesIO(blob)) as archive:
        manifest = archive.read("manifest.json")
        assert hashlib.sha256(manifest).hexdigest() == last["manifest_sha256"]
        for entry in json.loads(manifest)["files"]:
            assert hashlib.sha256(archive.read(entry["path"])).hexdigest() == entry["sha256"]
    _response(
        client,
        "POST",
        prefix + last["id"] + "/acknowledge",
        headers["customer"],
        {"manifest_sha256": first["manifest_sha256"], "reason": "错误旧版本不得签收新包"},
        expected=409,
    )
    body = {"manifest_sha256": last["manifest_sha256"], "reason": "恢复后客户确认收到准确版本"}
    signed = _response(
        client, "POST", prefix + last["id"] + "/acknowledge", headers["customer"], body
    ).json()
    again = _response(
        client, "POST", prefix + last["id"] + "/acknowledge", headers["customer"], body
    ).json()
    assert signed == again and signed["customer_receipt_status"] == "acknowledged"
    assert signed["external_import_status"] == "separate_workflow"
    return {
        "filing_original_and_version_restored": True,
        "handoff_and_provider_receipt_restored": True,
        "tax_independent_review_export": True,
        "invoice_original_verified": True,
        "exact_customer_acknowledgement": True,
        "cross_scope_denied": True,
    }
