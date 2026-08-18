"""Deterministic OpenAI-compatible model stub for the local RCA plumbing test."""

from __future__ import annotations

import json
import os
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

MAX_REQUEST_BYTES = 512 * 1024
API_KEY = os.getenv("MINISHOP_E2E_LLM_API_KEY", "minishop-e2e-local-key")
ROOT_CAUSES = {
    "checkout-service": {
        "fault_type": "latency",
        "detail": "injected processing latency before downstream calls",
        "recommendation": "Disable the checkout latency fault and verify p95 recovery.",
    },
    "inventory-service": {
        "fault_type": "db_timeout",
        "detail": "DB_TIMEOUT while reserving inventory",
        "recommendation": (
            "Reset the inventory timeout fault and verify reservation latency."
        ),
    },
    "payment-service": {
        "fault_type": "payment_error",
        "detail": "PAYMENT_GATEWAY_ERROR during checkout",
        "recommendation": (
            "Reset the payment error fault and verify payment success rate."
        ),
    },
}


class RCAStubHandler(BaseHTTPRequestHandler):
    server_version = "MiniShopRCAStub/1.0"

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._write_json(HTTPStatus.OK, {"status": "ok"})
            return
        self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._write_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        if self.headers.get("Authorization") != f"Bearer {API_KEY}":
            self._write_json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
            return
        try:
            request = self._read_request()
            report = build_report(request)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._write_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "invalid_request", "message": str(exc)[:256]},
            )
            return
        self._write_json(
            HTTPStatus.OK,
            {
                "id": "chatcmpl_minishop_e2e",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(report, separators=(",", ":")),
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def _read_request(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ValueError("Content-Length is required")
        length = int(raw_length)
        if not 1 <= length <= MAX_REQUEST_BYTES:
            raise ValueError("request size is invalid")
        document = json.loads(self.rfile.read(length))
        if not isinstance(document, dict):
            raise TypeError("request must be an object")
        return document

    def _write_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"llm_stub {self.address_string()} {format % args}", flush=True)


def build_report(request: dict[str, Any]) -> dict[str, Any]:
    messages = request["messages"]
    if not isinstance(messages, list) or not messages:
        raise TypeError("messages must be a non-empty list")
    user_message = messages[-1]
    evidence_payload = json.loads(user_message["content"])
    evidence = evidence_payload["evidence"]
    if not isinstance(evidence, list) or not evidence:
        raise TypeError("evidence must be a non-empty list")

    service_name = detect_service(evidence)
    evidence_ids = [item["evidence_id"] for item in evidence]
    deployment_change = has_payment_v2_deployment(evidence)
    if deployment_change and not has_deployment_corroboration(evidence):
        return {
            "conclusion_status": "UNDETERMINED",
            "title": "Deployment change requires corroboration",
            "summary": (
                "A payment-service deployment was observed, but Change Evidence "
                "alone is insufficient to attribute the incident."
            ),
            "confidence": 0.2,
            "evidence_ids": evidence_ids,
            "recommendations": [
                "Collect metric and log or trace evidence before attribution."
            ],
        }
    root_cause = ROOT_CAUSES.get(service_name)
    if deployment_change and has_deployment_corroboration(evidence):
        root_cause = {
            "fault_type": "deployment_regression",
            "detail": (
                "payment-service v2 errors began after the corroborated deployment"
            ),
            "recommendation": (
                "Review the v2 rollout and use the approved rollback procedure."
            ),
        }
    if root_cause is None:
        return {
            "conclusion_status": "UNDETERMINED",
            "title": "Root cause requires human review",
            "summary": (
                "The supplied evidence did not identify a supported MiniShop service."
            ),
            "confidence": 0.0,
            "evidence_ids": evidence_ids,
            "recommendations": ["Review the collected evidence manually."],
        }
    fault_type = root_cause["fault_type"]
    return {
        "conclusion_status": "CANDIDATE",
        "title": f"Candidate: {service_name} {fault_type}",
        "summary": (
            f"Evidence supports {service_name} fault_type {fault_type} as the "
            f"candidate root cause: {root_cause['detail']}."
        ),
        "confidence": 0.95,
        "evidence_ids": evidence_ids,
        "recommendations": [root_cause["recommendation"]],
    }


def detect_service(evidence: list[dict[str, Any]]) -> str:
    text = " ".join(
        f"{item.get('source', '')} {item.get('summary', '')}" for item in evidence
    )
    match = re.search(r"(checkout|inventory|payment)-service", text)
    return match.group(0) if match is not None else ""


def has_payment_v2_deployment(evidence: list[dict[str, Any]]) -> bool:
    """只识别明确的 DEPLOYMENT v1→v2 摘要，忽略无关配置变更。"""
    for item in evidence:
        if item.get("evidence_type") != "CHANGE":
            continue
        summary = str(item.get("summary", ""))
        normalized = summary.lower().replace(" ", "")
        if (
            "payment-service" in normalized
            and "deployment" in normalized
            and "v1->v2" in normalized
            and "status=succeeded" in normalized
            and is_change_temporally_correlated(summary)
        ):
            return True
    return False


def is_change_temporally_correlated(summary: str) -> bool:
    """E2E Stub 只把事故前一分钟内的发布视为候选变更。"""
    match = re.search(r"minutes_from_incident=(-?\d+(?:\.\d+)?)", summary.lower())
    if match is None:
        return False
    offset_minutes = float(match.group(1))
    return -1.0 <= offset_minutes <= 0.1


def has_deployment_corroboration(evidence: list[dict[str, Any]]) -> bool:
    """发布回归至少要求 Metric 与 Log/Trace 中的一类同时存在。"""
    evidence_types = {
        item.get("evidence_type") for item in evidence if isinstance(item, dict)
    }
    return "METRIC" in evidence_types and bool({"LOG", "TRACE"} & evidence_types)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8081), RCAStubHandler).serve_forever()
