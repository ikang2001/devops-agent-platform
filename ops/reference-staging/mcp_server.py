from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.getenv("REFERENCE_MCP_TOKEN", "reference-mcp-token-change-me")
PROTOCOL_VERSION = "2025-06-18"
TOOL_NAME = "reference.observability.lookup"


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "ReferenceMCP/1.0"

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send({"status": "ok", "synthetic": True})
            return
        self._send({"error": "not_found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/mcp":
            self._send({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send({"error": "unauthorized"}, HTTPStatus.UNAUTHORIZED)
            return
        request = self._read_request()
        if request is None:
            self._send_jsonrpc_error(None, -32700, "Parse error")
            return
        request_id = request.get("id")
        method = request.get("method")
        if request.get("jsonrpc") != "2.0" or not isinstance(method, str):
            self._send_jsonrpc_error(request_id, -32600, "Invalid Request")
            return
        if method == "initialize":
            self._send_result(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "reference-readonly", "version": "1.0"},
                    "instructions": "Synthetic read-only reference server.",
                },
            )
            return
        if method == "tools/list":
            self._send_result(request_id, {"tools": [self._tool_definition()]})
            return
        if method == "tools/call":
            self._call_tool(request_id, request.get("params"))
            return
        self._send_jsonrpc_error(request_id, -32601, "Method not found")

    def log_message(self, format: str, *args: object) -> None:
        return

    def _read_request(self) -> dict | None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if not 1 <= length <= 64 * 1024:
            return None
        try:
            request = json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            return None
        return request if isinstance(request, dict) else None

    def _call_tool(self, request_id: object, params: object) -> None:
        if not isinstance(params, dict) or params.get("name") != TOOL_NAME:
            self._send_jsonrpc_error(request_id, -32602, "Unknown tool")
            return
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            self._send_jsonrpc_error(request_id, -32602, "Invalid arguments")
            return
        key = arguments.get("key")
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            self._send_jsonrpc_error(request_id, -32602, "key is required")
            return
        result = {"found": key == "checkout-api", "key": key, "synthetic": True}
        self._send_result(
            request_id,
            {
                "content": [
                    {"type": "text", "text": json.dumps(result, separators=(",", ":"))}
                ],
                "structuredContent": result,
                "isError": False,
            },
        )

    @staticmethod
    def _tool_definition() -> dict[str, object]:
        return {
            "name": TOOL_NAME,
            "description": "Read synthetic observability reference data.",
            "inputSchema": {
                "type": "object",
                "properties": {"key": {"type": "string", "maxLength": 128}},
                "required": ["key"],
                "additionalProperties": False,
            },
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": True,
                "openWorldHint": False,
            },
        }

    def _send_result(self, request_id: object, result: object) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _send_jsonrpc_error(self, request_id: object, code: int, message: str) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            }
        )

    def _send(self, document: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(document, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("MCP-Protocol-Version", PROTOCOL_VERSION)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", 8080), MCPHandler).serve_forever()


if __name__ == "__main__":
    main()
