"""AK01 isolated process helpers: real workers and loopback-only HTTP transport."""

import ipaddress
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import httpx


def settings(base_url):
    from app.intelligence.provider import AISettings

    return AISettings(
        base_url=base_url,
        api_key="ak01-synthetic-not-a-secret",
        allow_http=True,
        vision_model="ak01-ocr",
        journal_model="ak01-journal",
        chat_model="ak01-chat",
        timeout_seconds=15,
    )


def pricing():
    from app.operations.pricing import Pricing, Rate

    return Pricing(
        ocr=Rate("1.000000", "2.000000"),
        proposal=Rate("1.000000", "2.000000"),
        chat=Rate("1.000000", "2.000000"),
    )


def runtime(db, storage_path, base_url, *, before_http_crash=False):
    from app.accounting.service import AccountingService
    from app.assistant.meter import AssistantMeter
    from app.assistant.provider import AssistantProvider
    from app.assistant.service import AssistantService
    from app.assistant.worker import AssistantWorker
    from app.business.service import BusinessService
    from app.business.storage import AttachmentStore
    from app.intelligence.provider import OpenAICompatibleProvider
    from app.intelligence.service import IntelligenceService
    from app.jobs.service import QueueService
    from app.jobs.worker import QueueWorker
    from app.operations.meter import MeterService

    class Transport(httpx.BaseTransport):
        def __init__(self):
            self.inner = httpx.HTTPTransport(retries=0)

        def handle_request(self, request):
            if before_http_crash:
                # Fault point is after production's durable sent marker and
                # reservation, before the first transport byte is transmitted.
                os._exit(73)
            return self.inner.handle_request(request)

        def close(self):
            self.inner.close()

    client = httpx.Client(transport=Transport(), timeout=15, trust_env=False)
    provider = OpenAICompatibleProvider(settings(base_url), client=client)
    ledger, business = AccountingService(db), BusinessService(db)
    intelligence = IntelligenceService(db, AttachmentStore(storage_path), provider)
    meter = MeterService(db, pricing=pricing())
    intelligence.meter = meter
    queue = QueueService(db, intelligence)
    business.proposal_queue = queue.proposals
    ledger.intelligence = intelligence
    chat_provider = AssistantProvider(provider)
    chat = AssistantService(
        db, ledger, business, provider=chat_provider, intelligence=intelligence
    )
    return SimpleNamespace(
        db=db,
        client=client,
        provider=provider,
        ledger=ledger,
        business=business,
        intelligence=intelligence,
        meter=meter,
        queue=queue,
        worker=QueueWorker(queue),
        chat=chat,
        chat_worker=AssistantWorker(chat, chat_provider, AssistantMeter(db, meter)),
    )


def worker_process(config, ready, start, result_path, before_http_crash=False):
    """Construct independent services/connections; never deserialize a live DB."""
    for name in tuple(os.environ):
        if name.startswith(("ACCOUNTING_", "SUPABASE_", "ZEABUR_", "VERCEL_", "OPENAI_")):
            os.environ.pop(name, None)
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def permitted(address):
        if not isinstance(address, tuple) or not ipaddress.ip_address(address[0]).is_loopback:
            raise AssertionError("AK01 subprocess attempted non-loopback access")

    def connect(sock, address):
        permitted(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        permitted(address)
        return original_connect_ex(sock, address)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    from app.accounting.database import Database

    db = None
    services = None
    try:
        db = Database(config["database"])
        services = runtime(
            db, config["storage"], config["base_url"], before_http_crash=before_http_crash is True
        )
        ready.set()
        if not start.wait(30):
            raise AssertionError("parent did not release subprocess start barrier")
        if before_http_crash == "after_claim":
            claimed = (
                services.chat_worker.claim()
                if config["kind"] == "chat"
                else services.queue.claim(services.worker.worker_id)
            )
            if not claimed:
                raise AssertionError("fault requires a durable real claim")
            os._exit(71)
        if before_http_crash == "after_settlement":
            original_settle = services.meter.settle

            def settled_then_crash(*args, **kwargs):
                result = original_settle(*args, **kwargs)
                if not result:
                    raise AssertionError("fault requires successful durable usage settlement")
                os._exit(74)

            services.meter.settle = settled_then_crash
        if config["kind"] == "chat":
            result = services.chat_worker.run_once()
        else:
            result = services.worker.run_once()
        Path(result_path).write_text(json.dumps({"ok": True, "result": result}), encoding="utf-8")
    except BaseException as error:
        Path(result_path).write_text(
            json.dumps({"ok": False, "type": type(error).__name__, "error": str(error)}),
            encoding="utf-8",
        )
        raise
    finally:
        if services:
            services.client.close()
        if db:
            db.close()

