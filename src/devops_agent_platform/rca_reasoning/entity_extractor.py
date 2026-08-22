from __future__ import annotations

import re
from dataclasses import dataclass

_ATTRIBUTE_PATTERN = re.compile(
    r'["\']?(?P<key>[a-z][a-z0-9_.-]{1,63})["\']?\s*[:=]\s*'
    r"(?:\"(?P<quoted>[^\"]+)\"|(?P<plain>[a-zA-Z0-9_./:@-]+))",
    re.IGNORECASE,
)
_SERVICE_PATTERN = re.compile(r"\b([a-z][a-z0-9-]+(?:-service|-api|-worker))\b", re.I)
_RESOURCE_PATTERN = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mongodb|redis|rabbitmq|elasticsearch|"
    r"memcached|kafka|s3|dns|[a-z][a-z0-9-]*(?:-primary|-pool|-queue))\b",
    re.IGNORECASE,
)
_CONTEXT_RESOURCE_PATTERN = re.compile(
    r"\b(?:request\s+to|dependency|resource)\s+"
    r"([a-z][a-z0-9_.:/@-]{1,255})\b",
    re.IGNORECASE,
)
_EMITTED_RESOURCE_PATTERN = re.compile(
    r"\b([a-z][a-z0-9_.:/@-]{1,255})\s+emitted\b",
    re.IGNORECASE,
)
_RESOURCE_STOP_WORDS = frozenset(
    {
        "call",
        "error",
        "failed",
        "failure",
        "latency",
        "operation",
        "request",
        "service",
        "timeout",
        "unavailable",
    }
)


@dataclass(frozen=True)
class EvidenceEntities:
    attributes: tuple[tuple[str, str], ...]
    service: str | None
    resource: str | None
    error_type: str | None
    version: str | None


class EvidenceEntityExtractor:
    """优先读取 OpenTelemetry/结构化字段，文本匹配只作为兼容回退。"""

    _SERVICE_KEYS = (
        "service.name",
        "service_name",
        "peer.service",
        "peer_service",
        "rpc.service",
        "rpc_service",
    )
    _RESOURCE_KEYS = (
        "server.address",
        "server_address",
        "resource",
        "resource.name",
        "resource_name",
        "resource.system",
        "resource_system",
        "minishop.fault.resource",
        "db.name",
        "db_name",
        "db.system",
        "db_system",
        "messaging.destination",
        "messaging_destination",
    )

    def extract(self, text: str) -> EvidenceEntities:
        attributes: dict[str, str] = {}
        for match in _ATTRIBUTE_PATTERN.finditer(text):
            key = match.group("key").casefold()
            value = match.group("quoted") or match.group("plain")
            if not value or value.casefold() in {"null", "none", "unknown", "n/a"}:
                continue
            # Loki returns newest entries first. Preserve the first meaningful
            # structured value so an older log in the same query window cannot
            # overwrite the current incident's error/resource identity.
            attributes.setdefault(key, value)
        service = self._first(attributes, self._SERVICE_KEYS)
        if service is None:
            match = _SERVICE_PATTERN.search(text)
            service = match.group(1).casefold() if match else None
        resource = self._first(attributes, self._RESOURCE_KEYS)
        if resource is None:
            match = _RESOURCE_PATTERN.search(text)
            resource = match.group(0).casefold() if match else None
        if resource is None:
            match = _CONTEXT_RESOURCE_PATTERN.search(text)
            candidate = match.group(1).casefold() if match else None
            resource = (
                candidate
                if candidate is not None and candidate not in _RESOURCE_STOP_WORDS
                else None
            )
        if resource is None:
            match = _EMITTED_RESOURCE_PATTERN.search(text)
            candidate = match.group(1).casefold() if match else None
            resource = (
                candidate
                if candidate is not None and candidate != service
                else None
            )
        error_type = self._first(
            attributes,
            (
                "error.type",
                "error_type",
                "fault.type",
                "fault_type",
                "minishop.fault.type",
            ),
        )
        version = self._first(
            attributes,
            ("deployment.version", "deployment_version", "service.version"),
        )
        return EvidenceEntities(
            attributes=tuple(sorted(attributes.items())),
            service=service,
            resource=resource,
            error_type=error_type,
            version=version,
        )

    @staticmethod
    def _first(attributes: dict[str, str], keys: tuple[str, ...]) -> str | None:
        for key in keys:
            value = attributes.get(key)
            if value:
                return value.casefold()
        return None
