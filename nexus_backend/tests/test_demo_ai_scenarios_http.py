"""Real localhost HTTP acceptance with synthetic vendors, dynamic ports and cleanup."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
import time

import pytest

from app.assistant.provider import AssistantProvider
from app.intelligence.provider import AISettings, OpenAICompatibleProvider, ProviderError
from test_intelligence_provider import envelope, extraction, page, suggestion


class SyntheticHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        # Request logging must not accidentally print synthetic authorization headers.
        pass

    def do_POST(self):
        content = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.server.calls.append({"path": self.path, "authorization": self.headers.get("authorization"), "payload": content})
        if self.server.response_mode == "timeout":
            time.sleep(1.25)
        if self.server.response_mode == "schema_invalid":
            data = {"answer": "wrong schema for journal", "citation_ids": [], "proposals": []}
        elif self.server.vendor == "ocr":
            data = extraction()
        elif "你是衡账" in content["messages"][0]["content"]:
            data = {"answer": "依据当前范围核对", "citation_ids": [], "proposals": []}
        else:
            data = suggestion()
        body = json.dumps(envelope(data), ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Expected when the real client times out; this must not trigger a resend.
            pass


@pytest.fixture
def synthetic_vendors():
    servers, threads = [], []
    try:
        for vendor in ("ocr", "journal_and_chat"):
            server = ThreadingHTTPServer(("127.0.0.1", 0), SyntheticHandler)
            server.vendor, server.response_mode, server.calls = vendor, "valid", []
            thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
            thread.start()
            servers.append(server)
            threads.append(thread)
        yield servers
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()  # Joins outstanding synthetic request handlers.
        for thread in threads:
            thread.join(timeout=2)
            assert not thread.is_alive()


def real_provider(servers):
    first, second = servers
    settings = AISettings(
        ocr_base_url=f"http://127.0.0.1:{first.server_port}/v1", ocr_api_key="synthetic-ocr-key", ocr_model="same-model",
        journal_base_url=f"http://127.0.0.1:{second.server_port}/v1", journal_api_key="synthetic-journal-key", journal_model="same-model",
        chat_base_url=f"http://127.0.0.1:{second.server_port}/v1", chat_api_key="synthetic-chat-key", chat_model="same-model",
        chat_response_format="json_object", allow_http=True, timeout_seconds=1,
    )
    # No MockTransport: the production httpx client must make actual HTTP requests.
    return OpenAICompatibleProvider(settings)


def test_two_actual_http_vendors_receive_only_their_scenes_despite_same_model(synthetic_vendors):
    provider = real_provider(synthetic_vendors)
    assert provider.extract_invoice([page()]).data.documents[0].gross_amount == "10.60"
    assert provider.suggest_journal({"currency": "CNY"}).data.description == "采购办公用品"
    assert AssistantProvider(provider).reply({"company_id": "synthetic-company"}, "说明", []).data.answer == "依据当前范围核对"
    first, second = synthetic_vendors
    assert [item["authorization"] for item in first.calls] == ["Bearer synthetic-ocr-key"]
    assert [item["authorization"] for item in second.calls] == ["Bearer synthetic-journal-key", "Bearer synthetic-chat-key"]
    assert all(item["path"] == "/v1/chat/completions" for item in first.calls + second.calls)
    assert all(item["payload"]["model"] == "same-model" for item in first.calls + second.calls)
    assert first.calls[0]["payload"]["response_format"]["json_schema"]["name"] == "InvoiceExtraction"
    assert second.calls[0]["payload"]["response_format"]["json_schema"]["name"] == "JournalSuggestion"
    assert second.calls[1]["payload"]["response_format"] == {"type": "json_object"}
    assert all("tools" not in item["payload"] and item["payload"]["store"] is False for item in first.calls + second.calls)


@pytest.mark.parametrize("failure", ["schema_invalid", "timeout"])
def test_real_http_invalid_format_and_unknown_timeout_never_retry_or_fallback(synthetic_vendors, failure):
    provider = real_provider(synthetic_vendors)
    first, second = synthetic_vendors
    second.response_mode = failure
    with pytest.raises(ProviderError) as caught:
        provider.suggest_journal({"currency": "CNY"})
    assert caught.value.code == failure and not caught.value.retryable
    assert first.calls == []
    assert len(second.calls) == 1 and second.calls[0]["authorization"] == "Bearer synthetic-journal-key"
    assert "synthetic-journal-key" not in str(caught.value)
