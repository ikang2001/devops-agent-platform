import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient
from pydantic import SecretStr

from devops_agent_platform.bootstrap.app import create_app
from devops_agent_platform.infrastructure.config.settings import Settings

SECRET = "alert-webhook-secret-with-at-least-32-bytes"
URL = "/api/v1/alerts"
PAYLOAD = {
    "tenant_id": "tenant-a",
    "source": "alertmanager",
    "service_name": "checkout-api",
    "severity": "CRITICAL",
    "summary": "5xx error rate is high",
    "starts_at": "2026-07-02T10:00:00Z",
    "fingerprint": "fp-001",
    "external_event_id": "evt-001",
}


def build_client() -> TestClient:
    """启用真实Webhook认证并保留骨架业务服务。"""
    settings = Settings(
        _env_file=None,
        alert_webhook_auth_enabled=True,
        alert_webhook_secret=SecretStr(SECRET),
    )
    return TestClient(
        create_app(settings=settings, runtime_enabled=False)
    )


def encode_payload() -> bytes:
    """固定JSON字节，证明签名校验基于原始正文。"""
    return json.dumps(
        PAYLOAD,
        separators=(",", ":"),
    ).encode()


def sign(timestamp: str, body: bytes) -> str:
    """生成与生产调用方一致的HMAC请求头。"""
    digest = hmac.new(
        SECRET.encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={digest}"


def test_valid_webhook_signature_reaches_application_service() -> None:
    """验签成功后才进入骨架服务并得到其501响应。"""
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


def test_missing_signature_fails_before_application_service() -> None:
    """启用认证后，匿名Webhook必须失败关闭。"""
    response = build_client().post(URL, json=PAYLOAD)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "HMAC-SHA256"
    assert response.json()["error"] == {
        "code": "AUTHENTICATION_REQUIRED",
        "message": "Authentication is required",
    }


def test_signature_cannot_be_reused_after_body_tampering() -> None:
    """代理或攻击者改写正文后，原签名必须失效。"""
    client = build_client()
    timestamp = str(int(time.time()))
    body = encode_payload()
    tampered = body.replace(b"checkout-api", b"billing-api")

    response = client.post(
        URL,
        content=tampered,
        headers={
            "Content-Type": "application/json",
            "X-DevOps-Agent-Timestamp": timestamp,
            "X-DevOps-Agent-Signature": sign(timestamp, body),
        },
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_expired_signature_is_rejected() -> None:
    """签名即使正确，超过重放窗口也不能进入业务服务。"""
    client = build_client()
    timestamp = str(int(time.time()) - 301)
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

    assert response.status_code == 401
