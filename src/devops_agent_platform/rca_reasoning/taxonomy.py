from __future__ import annotations

import re

from .models import RootCauseType


class RootCauseTaxonomyMapper:
    """把模型别名和 Evidence 文本收敛到当前 Benchmark 分类。"""

    _ALIASES = {
        "app_error": RootCauseType.APPLICATION_ERROR,
        "application_failure": RootCauseType.APPLICATION_ERROR,
        "database_timeout": RootCauseType.DEPENDENCY_TIMEOUT,
        "dependency_timeout": RootCauseType.DEPENDENCY_TIMEOUT,
        "connection_timeout": RootCauseType.DEPENDENCY_TIMEOUT,
        "dns_failure": RootCauseType.DEPENDENCY_TIMEOUT,
        "vendor_timeout": RootCauseType.DEPENDENCY_TIMEOUT,
        "db_timeout": RootCauseType.DEPENDENCY_TIMEOUT,
        "postgres_timeout": RootCauseType.DEPENDENCY_TIMEOUT,
        "provider_timeout": RootCauseType.DEPENDENCY_TIMEOUT,
        "redis_latency": RootCauseType.DEPENDENCY_LATENCY,
        "cache_latency": RootCauseType.DEPENDENCY_LATENCY,
        "query_latency": RootCauseType.DEPENDENCY_LATENCY,
        "shard_latency": RootCauseType.DEPENDENCY_LATENCY,
        "config_error": RootCauseType.CONFIGURATION_ERROR,
        "configuration_regression": RootCauseType.CONFIGURATION_ERROR,
        "release_regression": RootCauseType.DEPLOYMENT_REGRESSION,
        "pool_exhaustion": RootCauseType.RESOURCE_EXHAUSTION,
        "gc_pause": RootCauseType.RESOURCE_EXHAUSTION,
        "storage_throttling": RootCauseType.RESOURCE_EXHAUSTION,
        "rate_limit": RootCauseType.RESOURCE_EXHAUSTION,
        "false_positive": RootCauseType.NO_ACTIONABLE_ROOT_CAUSE,
    }

    def map_type(self, raw_type: str) -> RootCauseType:
        normalized = _normalize(raw_type)
        try:
            return RootCauseType(normalized)
        except ValueError:
            return self._ALIASES.get(normalized, RootCauseType.UNKNOWN)

    def infer_from_text(self, text: str) -> RootCauseType:
        normalized = _normalize(text)
        if _contains(
            normalized,
            "no_customer_impact",
            "no_failure",
            "no_failure_counter",
            "remains_successful",
            "false_positive",
        ):
            return RootCauseType.NO_ACTIONABLE_ROOT_CAUSE
        if _contains(
            normalized,
            "redis_latency",
            "cache_latency",
            "query_latency",
            "shard_latency",
        ):
            return RootCauseType.DEPENDENCY_LATENCY
        if _contains(
            normalized,
            "database_timeout",
            "dependency_timeout",
            "connection_timeout",
            "dns_failure",
            "vendor_timeout",
            "db_timeout",
            "postgres_timeout",
            "provider_timeout",
            "times_out",
            "deadline_exceeded",
            "operation_exceeded_deadline",
            "upstream_request_expired",
        ):
            return RootCauseType.DEPENDENCY_TIMEOUT
        if "configuration" in normalized or "config_regression" in normalized:
            return RootCauseType.CONFIGURATION_ERROR
        if _contains(normalized, "deployment_regression", "errors_increased_after"):
            return RootCauseType.DEPLOYMENT_REGRESSION
        if "known_error" in normalized or "known_fingerprint" in normalized:
            return RootCauseType.KNOWN_ERROR
        if _contains(
            normalized,
            "connection_pool",
            "pool_exhaust",
            "lock_contention",
            "consumer_backlog",
            "retry_storm",
            "memory_pressure",
            "heap_pressure",
            "gc_pause",
            "storage_throttling",
            "rate_limit",
        ):
            return RootCauseType.RESOURCE_EXHAUSTION
        if "cascade" in normalized:
            return RootCauseType.CASCADING_FAILURE
        if "latency" in normalized or "duration_exceeds" in normalized:
            return RootCauseType.LATENCY
        if _contains(
            normalized,
            "payment_gateway_error",
            "payment_error",
            "application_error",
            "live_fault",
        ):
            return RootCauseType.APPLICATION_ERROR
        return RootCauseType.UNKNOWN


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def _contains(value: str, *needles: str) -> bool:
    return any(needle in value for needle in needles)
