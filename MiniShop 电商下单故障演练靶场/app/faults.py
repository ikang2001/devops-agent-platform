import random
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.models import FaultRecord


@dataclass(frozen=True)
class FaultEvidenceMetadata:
    error_type: str
    resource_name: str | None


_FAULT_EVIDENCE_METADATA = {
    "payment_error": FaultEvidenceMetadata("payment_error", None),
    "deployment_regression": FaultEvidenceMetadata(
        "deployment_regression", "payment-service:v2"
    ),
    "db_timeout": FaultEvidenceMetadata("db_timeout", "postgres"),
    "latency": FaultEvidenceMetadata("latency", "checkout-service:latency-fault"),
    "config_regression": FaultEvidenceMetadata(
        "config_regression", "payment-service/config"
    ),
    "redis_latency": FaultEvidenceMetadata("redis_latency", "redis"),
    "connection_pool_exhaustion": FaultEvidenceMetadata(
        "connection_pool_exhaustion", "checkout-db-pool"
    ),
    "third_party_api_timeout": FaultEvidenceMetadata(
        "provider_timeout", "payment-provider"
    ),
    "cascading_failure": FaultEvidenceMetadata(
        "cascading_failure", "checkout-service"
    ),
    "known_error_repeat": FaultEvidenceMetadata("known_error", "payment-service"),
    "misleading_history": FaultEvidenceMetadata(
        "application_error", "payment-service"
    ),
    "false_positive_alert": FaultEvidenceMetadata("false_positive", "alert-rule"),
    "cpu_saturation": FaultEvidenceMetadata("resource_exhaustion", "checkout-cpu"),
    "memory_pressure": FaultEvidenceMetadata("memory_pressure", "checkout-memory"),
}


def fault_evidence_metadata(fault_type: str) -> FaultEvidenceMetadata:
    return _FAULT_EVIDENCE_METADATA.get(
        fault_type,
        FaultEvidenceMetadata(fault_type, None),
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FaultState:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._faults: Dict[Tuple[str, str], FaultRecord] = {}

    def enable(
        self,
        *,
        service_name: str,
        fault_type: str,
        error_rate: float,
        delay_ms: int,
        duration_seconds: int,
        created_by: str,
    ) -> FaultRecord:
        now = utcnow()
        record = FaultRecord(
            fault_id=f"flt_{uuid.uuid4().hex[:12]}",
            service_name=service_name,
            fault_type=fault_type,
            enabled=True,
            error_rate=error_rate,
            delay_ms=delay_ms,
            starts_at=now,
            expires_at=now + timedelta(seconds=duration_seconds),
            created_by=created_by,
        )
        with self._lock:
            self._faults[(service_name, fault_type)] = record
        return record

    def list_active(self) -> List[FaultRecord]:
        with self._lock:
            return [record for record in self._faults.values() if self._is_active(record)]

    def get_active(self, service_name: str, fault_type: str) -> Optional[FaultRecord]:
        with self._lock:
            record = self._faults.get((service_name, fault_type))
            if record and self._is_active(record):
                return record
            return None

    def reset(self) -> List[FaultRecord]:
        with self._lock:
            records = list(self._faults.values())
            self._faults.clear()
            return records

    def should_fail(self, service_name: str, fault_type: str) -> bool:
        record = self.get_active(service_name, fault_type)
        if not record:
            return False
        return random.random() < record.error_rate

    def delay_ms(self, service_name: str, fault_type: str) -> int:
        record = self.get_active(service_name, fault_type)
        return record.delay_ms if record else 0

    @staticmethod
    def _is_active(record: FaultRecord) -> bool:
        return record.enabled and record.expires_at > utcnow()


fault_state = FaultState()
