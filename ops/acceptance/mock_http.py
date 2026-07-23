from __future__ import annotations

import hashlib
import json
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_TICKETS: dict[str, str] = {}


def _json_bytes(document: object) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


class AcceptanceHandler(BaseHTTPRequestHandler):
    server_version = "Step4AcceptanceMock/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json({"status": "ok"})
            return
        if parsed.path == "/loki/api/v1/query_range":
            self._send_json(self._loki_response())
            return
        if parsed.path == "/api/search":
            self._send_json(self._tempo_response(parse_qs(parsed.query)))
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/query_range":
            self._discard_body()
            self._send_json(self._prometheus_response())
            return
        if parsed.path == "/v1/responses":
            body = self._read_json_body()
            self._send_json(self._llm_response(body))
            return
        if parsed.path == "/api/tickets":
            body = self._read_json_body()
            self._send_json(self._ticketing_response(body))
            return
        self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _discard_body(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not length:
            return {}
        data = self.rfile.read(length)
        try:
            document = json.loads(data)
        except json.JSONDecodeError:
            return {}
        return document if isinstance(document, dict) else {}

    def _send_json(
        self,
        document: object,
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        content = _json_bytes(document)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    @staticmethod
    def _prometheus_response() -> dict:
        timestamp = int(time.time())
        return {
            "status": "success",
            "data": {
                "resultType": "matrix",
                "result": [
                    {
                        "metric": {
                            "__name__": "up",
                            "job": "step4-acceptance",
                        },
                        "values": [[timestamp, "1"]],
                    }
                ],
            },
        }

    @staticmethod
    def _loki_response() -> dict:
        now_ns = str(int(time.time() * 1_000_000_000))
        return {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {"service_name": "step4-acceptance"},
                        "values": [[now_ns, "synthetic acceptance log line"]],
                    }
                ],
            },
        }

    @staticmethod
    def _tempo_response(params: dict[str, list[str]]) -> dict:
        limit = int((params.get("limit") or ["1"])[0])
        traces = []
        if limit > 0:
            traces.append(
                {
                    "traceID": "0123456789abcdef0123456789abcdef",
                    "rootServiceName": "step4-acceptance",
                    "rootTraceName": "synthetic acceptance trace",
                    "startTimeUnixNano": str(int(time.time() * 1_000_000_000)),
                    "durationMs": 12,
                    "spanSets": [
                        {
                            "spans": [
                                {
                                    "spanID": "0123456789abcdef",
                                    "name": "synthetic span",
                                }
                            ]
                        }
                    ],
                }
            )
        return {
            "traces": traces,
            "metrics": {
                "inspectedTraces": len(traces),
                "inspectedBytes": 512,
                "completedJobs": 1,
                "totalJobs": 1,
            },
        }

    @staticmethod
    def _llm_response(body: dict) -> dict:
        evidence_ids: list[str] = []
        raw_input = body.get("input")
        if isinstance(raw_input, str):
            try:
                payload = json.loads(raw_input)
                evidence = payload.get("evidence", [])
                if isinstance(evidence, list):
                    evidence_ids = [
                        item["evidence_id"]
                        for item in evidence
                        if isinstance(item, dict)
                        and isinstance(item.get("evidence_id"), str)
                    ]
            except json.JSONDecodeError:
                evidence_ids = []
        report = {
            "conclusion_status": "CANDIDATE",
            "title": "Synthetic acceptance RCA candidate",
            "summary": (
                "The local acceptance model endpoint returned a bounded JSON report."
            ),
            "confidence": 0.5,
            "evidence_ids": evidence_ids[:1],
            "recommendations": ["Close this synthetic acceptance report."],
        }
        return {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(
                                report,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        }
                    ],
                }
            ],
        }

    def _ticketing_response(self, body: dict) -> dict:
        idempotency_key = self.headers.get("Idempotency-Key", "")
        if not idempotency_key:
            seed = json.dumps(body, sort_keys=True, separators=(",", ":"))
            idempotency_key = hashlib.sha256(seed.encode()).hexdigest()
        ticket_id = _TICKETS.get(idempotency_key)
        if ticket_id is None:
            ticket_id = "STEP4-" + hashlib.sha256(
                idempotency_key.encode("utf-8")
            ).hexdigest()[:12].upper()
            _TICKETS[idempotency_key] = ticket_id
        return {
            "succeeded": True,
            "external_ticket_id": ticket_id,
            "external_ticket_url": f"https://tickets.example.test/{ticket_id}",
        }


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), AcceptanceHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
