"""持久化的本地 Ticketing 仿真服务。

它使用 SQLite 和真实 HTTP 幂等语义，不伪装成 Jira/ServiceNow；响应显式带
``simulation=true``，便于验收报告区分本地仿真与外部系统签字。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

DATABASE_PATH = Path(os.getenv("SIM_TICKETING_DB", "/data/tickets.sqlite3"))


def _connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            idempotency_key TEXT PRIMARY KEY,
            ticket_id TEXT NOT NULL,
            title TEXT NOT NULL,
            body_json TEXT NOT NULL
        )
        """
    )
    connection.commit()
    return connection


def _json_bytes(document: object) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


class TicketingSimulationHandler(BaseHTTPRequestHandler):
    server_version = "DevOpsTicketingSimulation/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send({"status": "ok", "simulation": True})
            return
        self._send({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/api/tickets":
            self._send({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        body = self._read_json()
        idempotency_key = self.headers.get("Idempotency-Key")
        if not idempotency_key:
            serialized = json.dumps(body, sort_keys=True, separators=(",", ":"))
            idempotency_key = hashlib.sha256(serialized.encode()).hexdigest()
        title = str(body.get("title", "Simulation incident"))[:512]
        with _connect() as connection:
            row = connection.execute(
                "SELECT ticket_id FROM tickets WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                ticket_id = "SIM-" + hashlib.sha256(
                    idempotency_key.encode()
                ).hexdigest()[:12].upper()
                connection.execute(
                    "INSERT INTO tickets "
                    "(idempotency_key, ticket_id, title, body_json) "
                    "VALUES (?, ?, ?, ?)",
                    (idempotency_key, ticket_id, title, json.dumps(body)),
                )
                connection.commit()
            else:
                ticket_id = row[0]
        self._send(
            {
                "succeeded": True,
                "external_ticket_id": ticket_id,
                "external_ticket_url": f"https://ticketing.simulation.invalid/{ticket_id}",
                "simulation": True,
                "idempotent": row is not None,
            }
        )

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 256 * 1024:
            return {}
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    def _send(
        self,
        document: object,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        content = _json_bytes(document)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    port = int(os.getenv("SIM_TICKETING_PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), TicketingSimulationHandler).serve_forever()


if __name__ == "__main__":
    main()
