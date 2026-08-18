from __future__ import annotations

import json
import os
import ssl
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ISSUER = os.getenv("REFERENCE_OIDC_ISSUER", "https://oidc:8443/")
AUDIENCE = os.getenv("REFERENCE_OIDC_AUDIENCE", "devops-agent-api")
KEY_ID = "reference-oidc-rs256-v1"
ALLOWED_SCOPES = frozenset(
    {
        "openid",
        "workspaces:read",
        "workspaces:write",
        "runbooks:write",
        "tool_permissions:read",
        "tool_permissions:write",
        "datasets:curate",
        "datasets:review",
        "datasets:publish",
        "datasets:read",
        "incidents:rca",
        "incidents:read",
        "rca:read",
    }
)


def _json_bytes(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


def _generate_tls_material() -> tuple[Path, Path]:
    cert_root = Path("/certs")
    cert_root.mkdir(parents=True, exist_ok=True)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Reference Staging CA")]
    )
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "oidc")])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName("oidc"), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = cert_root / "ca.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path = Path("/tmp/oidc-server.pem")
    key_path = Path("/tmp/oidc-server-key.pem")
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


SIGNING_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
JWK = jwt.algorithms.RSAAlgorithm.to_jwk(SIGNING_KEY.public_key(), as_dict=True)
JWK.update({"kid": KEY_ID, "alg": "RS256", "use": "sig"})


class OIDCHandler(BaseHTTPRequestHandler):
    server_version = "ReferenceOIDC/1.0"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send({"status": "ok", "synthetic": True})
            return
        if self.path == "/.well-known/openid-configuration":
            self._send(
                {
                    "issuer": ISSUER,
                    "jwks_uri": f"{ISSUER}.well-known/jwks.json",
                    "token_endpoint": f"{ISSUER}token",
                    "id_token_signing_alg_values_supported": ["RS256"],
                }
            )
            return
        if self.path == "/.well-known/jwks.json":
            self._send({"keys": [JWK]})
            return
        self._send({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/token":
            self._send({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        body = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        client_id = (form.get("client_id") or [""])[0]
        subjects = {
            "reference-cli": "reference-reviewer",
            "reference-cli-domain": "reference-domain-reviewer",
            "reference-cli-privacy": "reference-privacy-reviewer",
        }
        if client_id not in subjects:
            self._send({"error": "invalid_client"}, HTTPStatus.UNAUTHORIZED)
            return
        requested = set((form.get("scope") or ["openid"])[0].split())
        if not requested or not requested.issubset(ALLOWED_SCOPES):
            self._send({"error": "invalid_scope"}, HTTPStatus.BAD_REQUEST)
            return
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "iss": ISSUER,
                "aud": AUDIENCE,
                "sub": subjects[client_id],
                "iat": now,
                "nbf": now - timedelta(seconds=1),
                "exp": now + timedelta(minutes=15),
                "scope": " ".join(sorted(requested)),
                "tenant_ids": ["reference-tenant"],
                "all_tenants": False,
                "synthetic": True,
            },
            SIGNING_KEY,
            algorithm="RS256",
            headers={"kid": KEY_ID, "typ": "JWT"},
        )
        self._send(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": 900,
                "scope": " ".join(sorted(requested)),
                "synthetic": True,
            }
        )

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, document: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = _json_bytes(document)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    cert_path, key_path = _generate_tls_material()
    server = ThreadingHTTPServer(("0.0.0.0", 8443), OIDCHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
