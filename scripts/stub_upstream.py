"""Minimal OpenAI-compatible stub standing in for llama-server.

Implements exactly what LocalJev's Engine touches:
  GET  /v1/models            -> {"data": [{"id": MODEL}]}
  POST /v1/chat/completions  -> an OpenAI chat completion whose content is a
                                JSON object satisfying the json_schema the
                                caller sent (numbers for noul, normalized
                                arrays for choice/score).

Deterministic: probabilities are derived from the question id so runs are
reproducible.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

MODEL = "stub-model"
PORT = 8000


def _answer_for(prop: dict) -> object:
    if prop.get("type") == "number":
        return 0.82
    n = int(prop.get("minItems", 2))
    # first outcome gets the bulk, remainder spread evenly; sums to 1.0
    head = 0.6
    rest = (1.0 - head) / (n - 1) if n > 1 else 0.0
    return [round(head, 4)] + [round(rest, 4)] * (n - 1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # quiet
        sys.stderr.write("[stub] " + fmt % args + "\n")

    def _send(self, obj: dict, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") == "/v1/models":
            self._send({"object": "list", "data": [{"id": MODEL, "object": "model"}]})
            return
        self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._send({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        req = json.loads(self.rfile.read(length) or b"{}")
        schema = (
            req.get("response_format", {})
            .get("json_schema", {})
            .get("schema", {})
        )
        props = (
            schema.get("properties", {}).get("answers", {}).get("properties", {})
        )
        answers = {qid: _answer_for(spec) for qid, spec in props.items()}
        content = json.dumps({"answers": answers})
        self._send(
            {
                "id": "chatcmpl-stub",
                "object": "chat.completion",
                "model": req.get("model", MODEL),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 123, "completion_tokens": 45},
            }
        )


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
