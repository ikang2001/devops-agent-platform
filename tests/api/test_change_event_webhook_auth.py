import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient
from pydantic import SecretStr

from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.infrastructure.config.settings import Settings

SECRET = "change-webhook-secret-with-at-least-32-bytes"
URL = "/api/v1/change-events"
PAYLOAD = {
    "tenant_id": "tenant-a",
    "source": "argocd",
    "external_event_id": "deploy-payment-v2",
    "service_name": "payment-service",
    "resource_type": "deployment",
    "resource_id": "payment-service",
    "change_type": "DEPLOYMENT",
    "status": "SUCCEEDED",
    "version_before": "v1",
    "version_after": "v2",
    "operator_id": "deployment-bot",
    "summary": "payment-service upgraded from v1 to v2",
    "metadata": {"cluster": "minishop"},
    "started_at": "2026-08-17T14:02:00Z",
    "completed_at": "2026-08-17T14:03:00Z",
}


def build_client() -> TestClient:
    settings = Settings(
        _env_file=None,
        alert_webhook_auth_enabled=True,
        alert_webhook_secret=SecretStr(SECRET),
    )
    return TestClient(create_app(settings=settings, runtime_enabled=False))


def encode_payload() -> bytes:
    return json.dumps(PAYLOAD, separators=(",", ":")).encode()


def sign(timestamp: str, body: bytes) -> str:
    digest = hmac.new(
        SECRET.encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def test_change_event_requires_machine_signature() -> None:
    response = build_client().post(URL, json=PAYLOAD)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_valid_signature_reaches_change_event_application_service() -> None:
    client = build_client()
    timestamp = str(int(time.time()))
    body = encode_payload()

    response = client.post(
        URL,
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-DevOps-Agent-Timestamp": timestamp,
            "X-DevOps-Agent-Signature": sign(timestamp, body),
        },
    )

    assert response.status_code == 501
    assert response.json()["error"]["code"] == "NOT_IMPLEMENTED"
