from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from devops_agent_platform.domain.exceptions import AppValidationError, ConflictError


class TopologyNodeKind(StrEnum):
    SERVICE = "SERVICE"
    RESOURCE = "RESOURCE"


class TopologySource(StrEnum):
    STATIC = "STATIC"
    TRACE = "TRACE"
    CMDB = "CMDB"


def _text(name: str, value: str, maximum: int) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{name} is invalid")


def _timestamp(name: str, value: datetime) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise AppValidationError(f"{name} must include timezone information")


def _confidence(value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 <= float(value) <= 1
    ):
        raise AppValidationError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class ServiceNode:
    node_id: str
    tenant_id: str
    service_name: str
    environment: str
    source: TopologySource
    confidence: float = 1.0
    observed_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    ttl_seconds: int = 3600
    metadata: tuple[tuple[str, str], ...] = ()

    @property
    def kind(self) -> TopologyNodeKind:
        return TopologyNodeKind.SERVICE

    def __post_init__(self) -> None:
        _text("node_id", self.node_id, 128)
        _text("tenant_id", self.tenant_id, 128)
        _text("service_name", self.service_name, 256)
        _text("environment", self.environment, 64)
        if not isinstance(self.source, TopologySource):
            raise AppValidationError("source must be a TopologySource")
        _confidence(self.confidence)
        _timestamp("observed_at", self.observed_at)
        if (
            isinstance(self.ttl_seconds, bool)
            or not isinstance(self.ttl_seconds, int)
            or not 1 <= self.ttl_seconds <= 31_536_000
        ):
            raise AppValidationError("ttl_seconds must be between 1 and 31536000")
        _validate_metadata(self.metadata)

    def is_expired(self, now: datetime) -> bool:
        _timestamp("now", now)
        return now > self.observed_at + timedelta(seconds=self.ttl_seconds)


@dataclass(frozen=True)
class ResourceNode:
    node_id: str
    tenant_id: str
    resource_type: str
    resource_name: str
    environment: str
    source: TopologySource
    confidence: float = 1.0
    observed_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    ttl_seconds: int = 3600
    metadata: tuple[tuple[str, str], ...] = ()

    @property
    def kind(self) -> TopologyNodeKind:
        return TopologyNodeKind.RESOURCE

    def __post_init__(self) -> None:
        _text("node_id", self.node_id, 128)
        _text("tenant_id", self.tenant_id, 128)
        _text("resource_type", self.resource_type, 64)
        _text("resource_name", self.resource_name, 256)
        _text("environment", self.environment, 64)
        if not isinstance(self.source, TopologySource):
            raise AppValidationError("source must be a TopologySource")
        _confidence(self.confidence)
        _timestamp("observed_at", self.observed_at)
        if (
            isinstance(self.ttl_seconds, bool)
            or not isinstance(self.ttl_seconds, int)
            or not 1 <= self.ttl_seconds <= 31_536_000
        ):
            raise AppValidationError("ttl_seconds must be between 1 and 31536000")
        _validate_metadata(self.metadata)

    def is_expired(self, now: datetime) -> bool:
        _timestamp("now", now)
        return now > self.observed_at + timedelta(seconds=self.ttl_seconds)


@dataclass(frozen=True)
class DependencyEdge:
    edge_id: str
    tenant_id: str
    source_node_id: str
    target_node_id: str
    relation: str = "CALLS"
    source: TopologySource = TopologySource.STATIC
    confidence: float = 1.0
    observed_at: datetime = field(default_factory=lambda: datetime.now().astimezone())
    ttl_seconds: int = 3600
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("edge_id", self.edge_id, 128),
            ("tenant_id", self.tenant_id, 128),
            ("source_node_id", self.source_node_id, 128),
            ("target_node_id", self.target_node_id, 128),
            ("relation", self.relation, 64),
        ):
            _text(name, value, maximum)
        if self.source_node_id == self.target_node_id:
            raise AppValidationError("dependency edge cannot point to itself")
        if not isinstance(self.source, TopologySource):
            raise AppValidationError("source must be a TopologySource")
        _confidence(self.confidence)
        _timestamp("observed_at", self.observed_at)
        if (
            isinstance(self.ttl_seconds, bool)
            or not isinstance(self.ttl_seconds, int)
            or not 1 <= self.ttl_seconds <= 31_536_000
        ):
            raise AppValidationError("ttl_seconds must be between 1 and 31536000")
        _validate_metadata(self.metadata)

    @property
    def key(self) -> tuple[str, str, str]:
        return self.source_node_id, self.target_node_id, self.relation

    def is_expired(self, now: datetime) -> bool:
        _timestamp("now", now)
        return now > self.observed_at + timedelta(seconds=self.ttl_seconds)


@dataclass(frozen=True)
class TraceDependency:
    """从 Tempo span 关系派生拓扑时使用的最小、已脱敏输入。"""

    tenant_id: str
    caller_service: str
    callee_service: str
    environment: str = "default"
    trace_id: str = "trace"
    confidence: float = 0.8

    def __post_init__(self) -> None:
        for name, value, maximum in (
            ("tenant_id", self.tenant_id, 128),
            ("caller_service", self.caller_service, 256),
            ("callee_service", self.callee_service, 256),
            ("environment", self.environment, 64),
            ("trace_id", self.trace_id, 128),
        ):
            _text(name, value, maximum)
        if self.caller_service == self.callee_service:
            raise AppValidationError("trace dependency cannot point to itself")
        _confidence(self.confidence)


@dataclass(frozen=True)
class TopologyGraph:
    tenant_id: str
    nodes: tuple[ServiceNode | ResourceNode, ...]
    edges: tuple[DependencyEdge, ...]
    max_depth: int = 8

    def __post_init__(self) -> None:
        _text("tenant_id", self.tenant_id, 128)
        if (
            isinstance(self.max_depth, bool)
            or not isinstance(self.max_depth, int)
            or not 1 <= self.max_depth <= 32
        ):
            raise AppValidationError("max_depth must be between 1 and 32")
        node_ids = [node.node_id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ConflictError("topology contains duplicate nodes")
        for node in self.nodes:
            if node.tenant_id != self.tenant_id:
                raise ConflictError("topology node crosses tenant boundary")
        edge_keys: set[tuple[str, str, str]] = set()
        known = set(node_ids)
        for edge in self.edges:
            if (
                edge.tenant_id != self.tenant_id
                or edge.source_node_id not in known
                or edge.target_node_id not in known
            ):
                raise ConflictError(
                    "topology edge crosses tenant or references an unknown node"
                )
            if edge.key in edge_keys:
                raise ConflictError("topology contains duplicate edges")
            edge_keys.add(edge.key)

    @property
    def node_by_id(self) -> dict[str, ServiceNode | ResourceNode]:
        return {node.node_id: node for node in self.nodes}

    def outgoing(self, node_id: str) -> tuple[DependencyEdge, ...]:
        return tuple(
            sorted(
                (edge for edge in self.edges if edge.source_node_id == node_id),
                key=lambda edge: edge.key,
            )
        )

    def incoming(self, node_id: str) -> tuple[DependencyEdge, ...]:
        return tuple(
            sorted(
                (edge for edge in self.edges if edge.target_node_id == node_id),
                key=lambda edge: edge.key,
            )
        )

    def would_create_cycle(self, source_node_id: str, target_node_id: str) -> bool:
        if source_node_id == target_node_id:
            return True
        adjacency: dict[str, set[str]] = {}
        for edge in self.edges:
            adjacency.setdefault(edge.source_node_id, set()).add(edge.target_node_id)
        adjacency.setdefault(source_node_id, set()).add(target_node_id)
        pending = [target_node_id]
        visited: set[str] = set()
        while pending:
            current = pending.pop()
            if current == source_node_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, ()))
        return False


def _validate_metadata(metadata: tuple[tuple[str, str], ...]) -> None:
    if not isinstance(metadata, tuple) or len(metadata) > 32:
        raise AppValidationError("metadata must be a tuple with at most 32 items")
    keys: set[str] = set()
    for item in metadata:
        if not isinstance(item, tuple) or len(item) != 2:
            raise AppValidationError("metadata items must be key/value tuples")
        key, value = item
        _text("metadata key", key, 64)
        _text("metadata value", value, 512)
        if key in keys:
            raise AppValidationError("metadata keys must be unique")
        keys.add(key)


def metadata_from_mapping(values: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, dict):
        raise AppValidationError("metadata must be an object")
    return tuple(sorted((str(key), str(value)) for key, value in values.items()))
