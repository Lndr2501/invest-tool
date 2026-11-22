"""Kleiner OpenAI-kompatibler Test-Server (Chat Completions + Responses, Streaming, Werkzeug-Aufrufe).

Ablauf je Anfrage: Liegt noch kein Werkzeug-Ergebnis vor, ruft das „Modell“ ein Werkzeug auf (``TOOL``), sonst
antwortet es mit Text (inkl. <think>-Block, der gefiltert werden muss). ``REQUESTS`` speichert alle Anfragen.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REQUESTS: list[dict] = []
TOOL = {"name": "update_finance_profile", "arguments": json.dumps({"fields": {"income": 2800, "expenses": 1500}})}
ANSWER = ["<thi", "nk>überlege</think>Hab ich mir ", "gemerkt: 2.800 € netto. ", "Wofür sparst du?"]
NO_TOOLS = {"flag": False}  # True = Server lehnt "tools" ab
NO_REASONING = {"flag": False}  # True = Server lehnt Denktiefe ab


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _sse(self, events):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for e in events:
            self.wfile.write(b"data: " + json.dumps(e).encode() + b"\n\n")
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")

    def do_GET(self):
        if self.path.endswith("/models"):
            if self.headers.get("Authorization") != "Bearer sk-test":
                return self._send_json(401, {"error": "bad key"})
            return self._send_json(200, {"data": [{"id": "test-model"}, {"id": "other"}]})
        self._send_json(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        REQUESTS.append({"path": self.path, "body": body, "auth": self.headers.get("Authorization")})
        if NO_REASONING["flag"] and ("reasoning" in body or "reasoning_effort" in body):
            return self._send_json(400, {"error": {"message": "unknown parameter: reasoning"}})
        if body.get("tools") and NO_TOOLS["flag"]:
            return self._send_json(400, {"error": {"message": "tools are not supported by this model"}})
        if self.path.endswith("/chat/completions"):
            has_result = any(m.get("role") == "tool" for m in body["messages"])
            if body.get("tools") and not has_result:
                return self._sse([
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                        "name": TOOL["name"], "arguments": TOOL["arguments"][:10]}}]}}]},
                    {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {
                        "arguments": TOOL["arguments"][10:]}}]}}]},
                ])
            return self._sse([{"choices": [{"delta": {"content": t}}]} for t in ANSWER])
        if self.path.endswith("/responses"):
            has_result = any(i.get("type") == "function_call_output" for i in body["input"])
            if body.get("tools") and not has_result:
                return self._sse([
                    {"type": "response.created"},
                    {"type": "response.output_item.done", "item": {"type": "function_call", "call_id": "r1",
                                                                     "name": TOOL["name"], "arguments": TOOL["arguments"]}},
                    {"type": "response.completed"},
                ])
            return self._sse([{"type": "response.output_text.delta", "delta": t} for t in ANSWER]
                             + [{"type": "response.completed"}])
        self._send_json(404, {})


def start() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
