"""Explicit synthetic OpenAI-compatible HTTP fixture, loopback only; never a real model."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_json(200 if self.path == "/health" else 404, {"synthetic_fixture": True})

    def do_POST(self):
        if self.path != "/v1/chat/completions" or self.headers.get("Authorization") != "Bearer synthetic-assistant-fixture":
            return self.send_json(403, {"error": "fixture authorization"})
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 200000:
            return self.send_json(413, {})
        supplied = json.loads(self.rfile.read(size))
        assert supplied["model"] == "synthetic-assistant-fixture"
        assert supplied["store"] is False and supplied["stream"] is False and "tools" not in supplied
        assert supplied["max_tokens"] == 4096
        content = json.loads(supplied["messages"][1]["content"])
        context = content["current_authorized_evidence"]
        books, events = context["books"], context["events"]
        reply = {"answer": f"合成模型验收回复：当前范围为 {context['company_name']}。金额采用后台保存的资料，请继续会计复核。",
                 "citation_ids": [context["evidence"][0]["id"]] if context["evidence"] else [],
                 "proposals": [{"kind": "bank_drafts", "reason": "根据当前明确账套资料准备流水草稿预览"}] if books and not events and not context.get("draft") else []}
        self.send_json(200, {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)}}],
                             "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30}})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 18101), Fixture).serve_forever()
