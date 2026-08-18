from __future__ import annotations

import hashlib
import json
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_TICKETS: dict[str, str] = {}


def _json_bytes(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


def _report() -> dict[str, object]:
    return {
        "conclusion_status": "CANDIDATE",
        "title": "Reference staging RCA candidate",
        "summary": "Synthetic provider completed a bounded report contract call.",
        "confidence": 0.25,
        "evidence_ids": [],
        "recommendations": ["Replace the reference provider before real acceptance."],
    }


def _benchmark_prediction() -> dict[str, object]:
    """返回保守的无根因结果，用于验证 live Runner，不冒充模型质量。"""
    return {
        "root_cause": None,
        "conclusion_status": "NO_ACTIONABLE_ROOT_CAUSE",
        "confidence": 0.0,
        "evidence_ids": [],
        "claims": [],
        "causal_chain": [],
        "affected_services": [],
    }


def _llm_text(body: dict) -> str:
    serialized = json.dumps(body, ensure_ascii=False)
    document = (
        _benchmark_prediction()
        if "real-llm-rca" in serialized
        or "bounded incident investigation" in serialized
        else _report()
    )
    return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


class ProviderHandler(BaseHTTPRequestHandler):
    server_version = "ReferenceProvider/1.0"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send({"status": "ok", "synthetic": True})
            return
        self._send({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        body = self._read_json()
        if self.path == "/v1/chat/completions":
            time.sleep(float(os.getenv("REFERENCE_LLM_DELAY_SECONDS", "0")))
            text = _llm_text(body)
            self._send(
                {
                    "id": "chatcmpl-reference",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 64,
                        "completion_tokens": 48,
                        "total_tokens": 112,
                    },
                    "synthetic": True,
                }
            )
            return
        if self.path == "/v1/responses":
            time.sleep(float(os.getenv("REFERENCE_LLM_DELAY_SECONDS", "0")))
            text = _llm_text(body)
            self._send(
                {
                    "id": "resp-reference",
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": text}],
                        }
                    ],
                    "usage": {"input_tokens": 64, "output_tokens": 48},
                    "synthetic": True,
                }
            )
            return
        if self.path == "/api/tickets":
            self._send(self._ticket(body))
            return
        self._send({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 256 * 1024:
            return {}
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _ticket(self, body: dict) -> dict[str, object]:
        key = self.headers.get("Idempotency-Key")
        if not key:
            encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
            key = hashlib.sha256(encoded.encode()).hexdigest()
        ticket_id = _TICKETS.setdefault(
            key,
            "REF-" + hashlib.sha256(key.encode()).hexdigest()[:12].upper(),
        )
        return {
            "succeeded": True,
            "external_ticket_id": ticket_id,
            "external_ticket_url": f"https://tickets.reference.invalid/{ticket_id}",
            "synthetic": True,
        }

    def _send(self, document: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = _json_bytes(document)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", 8080), ProviderHandler).serve_forever()


if __name__ == "__main__":
    main()
