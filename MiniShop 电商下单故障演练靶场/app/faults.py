import random
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from app.models import FaultRecord


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
