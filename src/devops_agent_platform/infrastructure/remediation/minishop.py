import asyncio
import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from devops_agent_platform.domain.exceptions import (
    AppValidationError,
    ConflictError,
    ResourceNotFound,
)

Clock = Callable[[], datetime]
_SCENARIO_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PLAN_ID_PATTERN = re.compile(r"^rmp_[0-9a-f]{32}$")
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_ALLOWED_SCENARIOS = {
    "checkout-latency": (
        "/faults/checkout-latency",
        frozenset({"delay_ms", "duration_seconds", "created_by"}),
    ),
    "inventory-db-timeout": (
        "/faults/inventory-db-timeout",
        frozenset({"delay_ms", "duration_seconds", "created_by"}),
    ),
    "payment-error": (
        "/faults/payment-error",
        frozenset({"error_rate", "duration_seconds", "created_by"}),
    ),
}


class RemediationStatus(StrEnum):
    """演练修复计划的单向状态。"""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_FAILED = "ROLLBACK_FAILED"


@dataclass(frozen=True)
class MiniShopRemediationConfig:
    """MiniShop 演练执行器的固定信任边界。"""

    scenario_directory: Path
    target_base_url: str
    enabled: bool = False
    allow_insecure_http: bool = False
    request_timeout_seconds: float = 5.0
    max_response_bytes: int = 64 * 1024

    def __post_init__(self) -> None:
        scenario_directory = self.scenario_directory.resolve()
        if not scenario_directory.is_dir():
            raise AppValidationError("scenario_directory is invalid")
        object.__setattr__(self, "scenario_directory", scenario_directory)
        parsed = urlparse(self.target_base_url)
        if (
            self.target_base_url != self.target_base_url.strip()
            or self.target_base_url.endswith("/")
            or parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise AppValidationError("target_base_url is invalid")
        if parsed.scheme == "http" and not self.allow_insecure_http:
            raise AppValidationError(
                "HTTP MiniShop target requires allow_insecure_http"
            )
        if (
            isinstance(self.request_timeout_seconds, bool)
            or not isinstance(self.request_timeout_seconds, int | float)
            or not 0 < float(self.request_timeout_seconds) <= 30
        ):
            raise AppValidationError("request_timeout_seconds is invalid")
        if (
            isinstance(self.max_response_bytes, bool)
            or not isinstance(self.max_response_bytes, int)
            or not 1 <= self.max_response_bytes <= 1024 * 1024
        ):
            raise AppValidationError("max_response_bytes is invalid")


@dataclass(frozen=True)
class RemediationPlan:
    """由受信 Manifest 派生且可持久化复核的修复计划。"""

    plan_id: str
    scenario_id: str
    scenario_title: str
    target_base_url: str
    manifest_digest: str
    plan_digest: str
    remediation_path: str
    remediation_body: dict[str, Any]
    remediation_expected_status: int
    rollback_path: str
    rollback_body: dict[str, Any]
    rollback_expected_status: int
    status: RemediationStatus
    version: int
    created_by: str
    approved_by: str | None
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    last_failure_code: str | None = None
    is_duplicate: bool = False

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["status"] = self.status.value
        for field_name in ("expires_at", "created_at", "updated_at"):
            result[field_name] = getattr(self, field_name).isoformat()
        return result


@dataclass(frozen=True)
class RemediationAuditEvent:
    event_id: int
    plan_id: str
    action: str
    actor: str
    trace_id: str
    payload_digest: str
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["created_at"] = self.created_at.isoformat()
        return result


class SQLiteRemediationApprovalStore:
    """演练专用 SQLite 审批与审计存储。

    SQLite 仅用于本机靶场；生产部署应替换为平台 PostgreSQL 事务适配器。
    """

    def __init__(self, database_path: Path) -> None:
        resolved = database_path.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._database_path = resolved
        self._initialize()

    def create(self, plan: RemediationPlan, trace_id: str) -> RemediationPlan:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO remediation_plans (
                        plan_id, plan_json, status, version, created_by,
                        approved_by, expires_at, created_at, updated_at,
                        approval_key_hash, execution_key_hash,
                        rollback_key_hash, last_failure_code
                    ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL, NULL, NULL)
                    """,
                    (
                        plan.plan_id,
                        self._encode_plan(plan),
                        plan.status.value,
                        plan.version,
                        plan.created_by,
                        plan.expires_at.isoformat(),
                        plan.created_at.isoformat(),
                        plan.updated_at.isoformat(),
                    ),
                )
                self._append_audit(
                    connection,
                    plan,
                    "PLAN_CREATED",
                    plan.created_by,
                    trace_id,
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                raise ConflictError("Remediation plan already exists") from None
        return plan

    def get(self, plan_id: str) -> RemediationPlan:
        _validate_plan_id(plan_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM remediation_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFound("Remediation plan not found")
        return self._decode_plan(row)

    def approve(
        self,
        *,
        plan_id: str,
        expected_version: int,
        approved_by: str,
        idempotency_key: str,
        trace_id: str,
        now: datetime,
    ) -> RemediationPlan:
        return self._transition(
            plan_id=plan_id,
            expected_version=expected_version,
            from_status=RemediationStatus.DRAFT,
            to_status=RemediationStatus.APPROVED,
            actor=approved_by,
            idempotency_key=idempotency_key,
            idempotency_column="approval_key_hash",
            action="PLAN_APPROVED",
            trace_id=trace_id,
            now=now,
            require_unexpired=True,
            approved_by=approved_by,
        )

    def begin_execution(
        self,
        *,
        plan_id: str,
        expected_version: int,
        actor: str,
        idempotency_key: str,
        trace_id: str,
        now: datetime,
    ) -> RemediationPlan:
        return self._transition(
            plan_id=plan_id,
            expected_version=expected_version,
            from_status=RemediationStatus.APPROVED,
            to_status=RemediationStatus.EXECUTING,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_column="execution_key_hash",
            action="EXECUTION_STARTED",
            trace_id=trace_id,
            now=now,
            require_unexpired=True,
        )

    def finish_execution(
        self,
        *,
        plan_id: str,
        expected_version: int,
        actor: str,
        trace_id: str,
        now: datetime,
        succeeded: bool,
        failure_code: str | None = None,
    ) -> RemediationPlan:
        return self._transition(
            plan_id=plan_id,
            expected_version=expected_version,
            from_status=RemediationStatus.EXECUTING,
            to_status=(
                RemediationStatus.EXECUTED if succeeded else RemediationStatus.FAILED
            ),
            actor=actor,
            idempotency_key=None,
            idempotency_column=None,
            action=("EXECUTION_SUCCEEDED" if succeeded else "EXECUTION_FAILED"),
            trace_id=trace_id,
            now=now,
            failure_code=failure_code,
        )

    def begin_rollback(
        self,
        *,
        plan_id: str,
        expected_version: int,
        actor: str,
        idempotency_key: str,
        trace_id: str,
        now: datetime,
    ) -> RemediationPlan:
        return self._transition(
            plan_id=plan_id,
            expected_version=expected_version,
            from_status=RemediationStatus.EXECUTED,
            to_status=RemediationStatus.ROLLING_BACK,
            actor=actor,
            idempotency_key=idempotency_key,
            idempotency_column="rollback_key_hash",
            action="ROLLBACK_STARTED",
            trace_id=trace_id,
            now=now,
        )

    def finish_rollback(
        self,
        *,
        plan_id: str,
        expected_version: int,
        actor: str,
        trace_id: str,
        now: datetime,
        succeeded: bool,
        failure_code: str | None = None,
    ) -> RemediationPlan:
        return self._transition(
            plan_id=plan_id,
            expected_version=expected_version,
            from_status=RemediationStatus.ROLLING_BACK,
            to_status=(
                RemediationStatus.ROLLED_BACK
                if succeeded
                else RemediationStatus.ROLLBACK_FAILED
            ),
            actor=actor,
            idempotency_key=None,
            idempotency_column=None,
            action=("ROLLBACK_SUCCEEDED" if succeeded else "ROLLBACK_FAILED"),
            trace_id=trace_id,
            now=now,
            failure_code=failure_code,
        )

    def list_audit(self, plan_id: str) -> tuple[RemediationAuditEvent, ...]:
        _validate_plan_id(plan_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, plan_id, action, actor, trace_id,
                       payload_digest, created_at
                  FROM remediation_audit
                 WHERE plan_id = ?
                 ORDER BY event_id ASC
                """,
                (plan_id,),
            ).fetchall()
        return tuple(
            RemediationAuditEvent(
                event_id=row["event_id"],
                plan_id=row["plan_id"],
                action=row["action"],
                actor=row["actor"],
                trace_id=row["trace_id"],
                payload_digest=row["payload_digest"],
                created_at=_parse_datetime(row["created_at"]),
            )
            for row in rows
        )

    def _transition(
        self,
        *,
        plan_id: str,
        expected_version: int,
        from_status: RemediationStatus,
        to_status: RemediationStatus,
        actor: str,
        idempotency_key: str | None,
        idempotency_column: str | None,
        action: str,
        trace_id: str,
        now: datetime,
        require_unexpired: bool = False,
        approved_by: str | None = None,
        failure_code: str | None = None,
    ) -> RemediationPlan:
        _validate_plan_id(plan_id)
        _validate_version(expected_version)
        _validate_identity("actor", actor)
        _validate_trace_id(trace_id)
        now = _validate_time("now", now)
        key_hash = (
            _hash_idempotency_key(idempotency_key)
            if idempotency_key is not None
            else None
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM remediation_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise ResourceNotFound("Remediation plan not found")
            current = self._decode_plan(row)
            if (
                idempotency_column is not None
                and row[idempotency_column] == key_hash
                and current.status is to_status
            ):
                connection.rollback()
                return replace(current, is_duplicate=True)
            if current.status is not from_status:
                connection.rollback()
                raise ConflictError(f"Remediation plan must be {from_status.value}")
            if current.version != expected_version:
                connection.rollback()
                raise ConflictError("Remediation plan version conflict")
            if require_unexpired and now >= current.expires_at:
                connection.rollback()
                raise ConflictError("Remediation plan approval has expired")
            if to_status is RemediationStatus.APPROVED:
                if actor == current.created_by:
                    connection.rollback()
                    raise ConflictError(
                        "Remediation approval requires a different operator"
                    )
                approved_by = actor
            next_plan = replace(
                current,
                status=to_status,
                version=current.version + 1,
                approved_by=approved_by or current.approved_by,
                updated_at=now,
                last_failure_code=failure_code,
                is_duplicate=False,
            )
            assignments = [
                "plan_json = ?",
                "status = ?",
                "version = ?",
                "approved_by = ?",
                "updated_at = ?",
                "last_failure_code = ?",
            ]
            values: list[Any] = [
                self._encode_plan(next_plan),
                next_plan.status.value,
                next_plan.version,
                next_plan.approved_by,
                now.isoformat(),
                failure_code,
            ]
            if idempotency_column is not None:
                assignments.append(f"{idempotency_column} = ?")
                values.append(key_hash)
            values.extend((plan_id, expected_version, from_status.value))
            updated = connection.execute(
                f"""
                UPDATE remediation_plans
                   SET {", ".join(assignments)}
                 WHERE plan_id = ? AND version = ? AND status = ?
                """,
                values,
            )
            if updated.rowcount != 1:
                connection.rollback()
                raise ConflictError("Remediation plan changed concurrently")
            self._append_audit(
                connection,
                next_plan,
                action,
                actor,
                trace_id,
            )
            connection.commit()
        return next_plan

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS remediation_plans (
                    plan_id TEXT PRIMARY KEY,
                    plan_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    created_by TEXT NOT NULL,
                    approved_by TEXT,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    approval_key_hash TEXT,
                    execution_key_hash TEXT,
                    rollback_key_hash TEXT,
                    last_failure_code TEXT
                );
                CREATE TABLE IF NOT EXISTS remediation_audit (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(plan_id) REFERENCES remediation_plans(plan_id)
                );
                CREATE INDEX IF NOT EXISTS ix_remediation_audit_plan
                    ON remediation_audit(plan_id, event_id);
                CREATE TRIGGER IF NOT EXISTS tr_remediation_audit_no_update
                BEFORE UPDATE ON remediation_audit
                BEGIN
                    SELECT RAISE(ABORT, 'remediation audit is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS tr_remediation_audit_no_delete
                BEFORE DELETE ON remediation_audit
                BEGIN
                    SELECT RAISE(ABORT, 'remediation audit is append-only');
                END;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self._database_path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _append_audit(
        connection: sqlite3.Connection,
        plan: RemediationPlan,
        action: str,
        actor: str,
        trace_id: str,
    ) -> None:
        payload_digest = hashlib.sha256(
            _canonical_json(
                {
                    "plan_id": plan.plan_id,
                    "plan_digest": plan.plan_digest,
                    "status": plan.status.value,
                    "version": plan.version,
                    "failure_code": plan.last_failure_code,
                }
            )
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO remediation_audit (
                plan_id, action, actor, trace_id, payload_digest, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                plan.plan_id,
                action,
                actor,
                trace_id,
                payload_digest,
                plan.updated_at.isoformat(),
            ),
        )

    @staticmethod
    def _encode_plan(plan: RemediationPlan) -> str:
        return _canonical_json(plan.to_dict()).decode("utf-8")

    @staticmethod
    def _decode_plan(row: sqlite3.Row) -> RemediationPlan:
        try:
            document = json.loads(row["plan_json"])
            return RemediationPlan(
                plan_id=document["plan_id"],
                scenario_id=document["scenario_id"],
                scenario_title=document["scenario_title"],
                target_base_url=document["target_base_url"],
                manifest_digest=document["manifest_digest"],
                plan_digest=document["plan_digest"],
                remediation_path=document["remediation_path"],
                remediation_body=document["remediation_body"],
                remediation_expected_status=(document["remediation_expected_status"]),
                rollback_path=document["rollback_path"],
                rollback_body=document["rollback_body"],
                rollback_expected_status=document["rollback_expected_status"],
                status=RemediationStatus(row["status"]),
                version=row["version"],
                created_by=row["created_by"],
                approved_by=row["approved_by"],
                expires_at=_parse_datetime(row["expires_at"]),
                created_at=_parse_datetime(row["created_at"]),
                updated_at=_parse_datetime(row["updated_at"]),
                last_failure_code=row["last_failure_code"],
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise RuntimeError("Stored remediation plan is invalid") from None


class MiniShopRemediationService:
    """只执行三份已登记 Manifest 中的修复与回滚动作。"""

    def __init__(
        self,
        config: MiniShopRemediationConfig,
        store: SQLiteRemediationApprovalStore,
        *,
        http_client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(config.request_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )
        self._closed = False

    def create_plan(
        self,
        *,
        scenario_id: str,
        created_by: str,
        trace_id: str,
        ttl_seconds: int = 900,
    ) -> RemediationPlan:
        _validate_scenario_id(scenario_id)
        _validate_identity("created_by", created_by)
        _validate_trace_id(trace_id)
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 60 <= ttl_seconds <= 3600
        ):
            raise AppValidationError("ttl_seconds is invalid")
        scenario, manifest_digest = self._load_scenario(scenario_id)
        now = self._now()
        plan_spec = {
            "scenario_id": scenario_id,
            "target_base_url": self._config.target_base_url,
            "remediation": scenario["cleanup"],
            "rollback": scenario["injection"],
            "manifest_digest": manifest_digest,
        }
        identity_digest = hashlib.sha256(
            _canonical_json(
                {
                    "scenario_id": scenario_id,
                    "created_by": created_by,
                    "trace_id": trace_id,
                    "created_at": now.isoformat(),
                }
            )
        ).hexdigest()
        plan = RemediationPlan(
            plan_id=f"rmp_{identity_digest[:32]}",
            scenario_id=scenario_id,
            scenario_title=scenario["title"],
            target_base_url=self._config.target_base_url,
            manifest_digest=manifest_digest,
            plan_digest=hashlib.sha256(_canonical_json(plan_spec)).hexdigest(),
            remediation_path=scenario["cleanup"]["path"],
            remediation_body=scenario["cleanup"]["json_body"],
            remediation_expected_status=scenario["cleanup"]["expected_status"],
            rollback_path=scenario["injection"]["path"],
            rollback_body=scenario["injection"]["json_body"],
            rollback_expected_status=scenario["injection"]["expected_status"],
            status=RemediationStatus.DRAFT,
            version=1,
            created_by=created_by,
            approved_by=None,
            expires_at=now + timedelta(seconds=ttl_seconds),
            created_at=now,
            updated_at=now,
        )
        return self._store.create(plan, trace_id)

    def approve(
        self,
        *,
        plan_id: str,
        expected_version: int,
        approved_by: str,
        idempotency_key: str,
        trace_id: str,
    ) -> RemediationPlan:
        return self._store.approve(
            plan_id=plan_id,
            expected_version=expected_version,
            approved_by=approved_by,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            now=self._now(),
        )

    async def execute(
        self,
        *,
        plan_id: str,
        expected_version: int,
        actor: str,
        idempotency_key: str,
        trace_id: str,
    ) -> RemediationPlan:
        self._require_execution_enabled()
        current = self._store.get(plan_id)
        self._verify_manifest(current)
        executing = self._store.begin_execution(
            plan_id=plan_id,
            expected_version=expected_version,
            actor=actor,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            now=self._now(),
        )
        try:
            await self._post_action(
                executing.target_base_url,
                executing.remediation_path,
                executing.remediation_body,
                executing.remediation_expected_status,
                idempotency_key,
                trace_id,
            )
        except asyncio.CancelledError:
            self._store.finish_execution(
                plan_id=plan_id,
                expected_version=executing.version,
                actor=actor,
                trace_id=trace_id,
                now=self._now(),
                succeeded=False,
                failure_code="cancelled",
            )
            raise
        except Exception as exc:
            failure_code = _failure_code(exc)
            return self._store.finish_execution(
                plan_id=plan_id,
                expected_version=executing.version,
                actor=actor,
                trace_id=trace_id,
                now=self._now(),
                succeeded=False,
                failure_code=failure_code,
            )
        return self._store.finish_execution(
            plan_id=plan_id,
            expected_version=executing.version,
            actor=actor,
            trace_id=trace_id,
            now=self._now(),
            succeeded=True,
        )

    async def rollback(
        self,
        *,
        plan_id: str,
        expected_version: int,
        actor: str,
        idempotency_key: str,
        trace_id: str,
    ) -> RemediationPlan:
        self._require_execution_enabled()
        current = self._store.get(plan_id)
        self._verify_manifest(current)
        rolling_back = self._store.begin_rollback(
            plan_id=plan_id,
            expected_version=expected_version,
            actor=actor,
            idempotency_key=idempotency_key,
            trace_id=trace_id,
            now=self._now(),
        )
        try:
            await self._post_action(
                rolling_back.target_base_url,
                rolling_back.rollback_path,
                rolling_back.rollback_body,
                rolling_back.rollback_expected_status,
                idempotency_key,
                trace_id,
            )
        except asyncio.CancelledError:
            self._store.finish_rollback(
                plan_id=plan_id,
                expected_version=rolling_back.version,
                actor=actor,
                trace_id=trace_id,
                now=self._now(),
                succeeded=False,
                failure_code="cancelled",
            )
            raise
        except Exception as exc:
            failure_code = _failure_code(exc)
            return self._store.finish_rollback(
                plan_id=plan_id,
                expected_version=rolling_back.version,
                actor=actor,
                trace_id=trace_id,
                now=self._now(),
                succeeded=False,
                failure_code=failure_code,
            )
        return self._store.finish_rollback(
            plan_id=plan_id,
            expected_version=rolling_back.version,
            actor=actor,
            trace_id=trace_id,
            now=self._now(),
            succeeded=True,
        )

    def get(self, plan_id: str) -> RemediationPlan:
        return self._store.get(plan_id)

    def list_audit(self, plan_id: str) -> tuple[RemediationAuditEvent, ...]:
        return self._store.list_audit(plan_id)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http_client.aclose()

    def _verify_manifest(self, plan: RemediationPlan) -> None:
        scenario, manifest_digest = self._load_scenario(plan.scenario_id)
        actions_match = (
            plan.target_base_url == self._config.target_base_url
            and plan.remediation_path == scenario["cleanup"]["path"]
            and plan.remediation_body == scenario["cleanup"]["json_body"]
            and plan.remediation_expected_status
            == scenario["cleanup"]["expected_status"]
            and plan.rollback_path == scenario["injection"]["path"]
            and plan.rollback_body == scenario["injection"]["json_body"]
            and plan.rollback_expected_status
            == scenario["injection"]["expected_status"]
        )
        plan_spec = {
            "scenario_id": plan.scenario_id,
            "target_base_url": plan.target_base_url,
            "remediation": scenario["cleanup"],
            "rollback": scenario["injection"],
            "manifest_digest": manifest_digest,
        }
        current_plan_digest = hashlib.sha256(_canonical_json(plan_spec)).hexdigest()
        if (
            not actions_match
            or manifest_digest != plan.manifest_digest
            or current_plan_digest != plan.plan_digest
        ):
            raise ConflictError("Scenario manifest changed after remediation planning")

    def _load_scenario(
        self,
        scenario_id: str,
    ) -> tuple[dict[str, Any], str]:
        _validate_scenario_id(scenario_id)
        path = (self._config.scenario_directory / f"{scenario_id}.json").resolve()
        if path.parent != self._config.scenario_directory or not path.is_file():
            raise ResourceNotFound("Scenario manifest not found")
        try:
            document = json.loads(
                path.read_text(encoding="utf-8"),
                parse_constant=_reject_json_constant,
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise AppValidationError("Scenario manifest is invalid") from None
        _validate_scenario_manifest(document, scenario_id)
        digest = hashlib.sha256(_canonical_json(document)).hexdigest()
        return document, digest

    async def _post_action(
        self,
        base_url: str,
        path: str,
        body: dict[str, Any],
        expected_status: int,
        idempotency_key: str,
        trace_id: str,
    ) -> None:
        if self._closed:
            raise RuntimeError("MiniShop remediation service is closed")
        content_size = 0
        try:
            async with self._http_client.stream(
                "POST",
                f"{base_url}{path}",
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Idempotency-Key": idempotency_key,
                    "X-Trace-Id": trace_id,
                },
                json=body,
                timeout=self._config.request_timeout_seconds,
            ) as response:
                async for chunk in response.aiter_bytes():
                    content_size += len(chunk)
                    if content_size > self._config.max_response_bytes:
                        raise RuntimeError("response_too_large")
                if response.status_code != expected_status:
                    raise RuntimeError(f"unexpected_status_{response.status_code}")
        except httpx.HTTPError:
            raise RuntimeError("transport_error") from None

    def _require_execution_enabled(self) -> None:
        if not self._config.enabled:
            raise ConflictError("MiniShop remediation kill switch is disabled")
        if self._closed:
            raise ConflictError("MiniShop remediation service is closed")

    def _now(self) -> datetime:
        return _validate_time("clock", self._clock())


def _validate_scenario_manifest(document: object, scenario_id: str) -> None:
    if not isinstance(document, dict):
        raise AppValidationError("Scenario manifest must be an object")
    allowed = _ALLOWED_SCENARIOS.get(scenario_id)
    if allowed is None or document.get("scenario_id") != scenario_id:
        raise AppValidationError("Scenario is not allowlisted")
    injection_path, injection_keys = allowed
    title = document.get("title")
    if (
        not isinstance(title, str)
        or not 1 <= len(title) <= 256
        or title != title.strip()
    ):
        raise AppValidationError("Scenario title is invalid")
    injection = _validate_action(
        document.get("injection"),
        expected_path=injection_path,
        allowed_body_keys=injection_keys,
    )
    cleanup = _validate_action(
        document.get("cleanup"),
        expected_path="/faults/reset",
        allowed_body_keys=frozenset(),
    )
    if injection["expected_status"] != 200 or cleanup["expected_status"] != 200:
        raise AppValidationError("Scenario action status is invalid")


def _validate_action(
    value: object,
    *,
    expected_path: str,
    allowed_body_keys: frozenset[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppValidationError("Scenario action is invalid")
    body = value.get("json_body")
    if (
        value.get("method") != "POST"
        or value.get("path") != expected_path
        or value.get("expected_status") != 200
        or not isinstance(body, dict)
        or frozenset(body) != allowed_body_keys
    ):
        raise AppValidationError("Scenario action is not allowlisted")
    if len(_canonical_json(body)) > 4096:
        raise AppValidationError("Scenario action body is too large")
    for item in body.values():
        if isinstance(item, bool) or not isinstance(item, str | int | float):
            raise AppValidationError("Scenario action value is invalid")
        if isinstance(item, int | float) and not math.isfinite(float(item)):
            raise AppValidationError("Scenario action number is invalid")
        if isinstance(item, str) and (
            not 1 <= len(item) <= 128
            or item != item.strip()
            or any(ord(character) < 32 or ord(character) == 127 for character in item)
        ):
            raise AppValidationError("Scenario action text is invalid")
    return value


def _validate_scenario_id(value: str) -> None:
    if not isinstance(value, str) or not _SCENARIO_ID_PATTERN.fullmatch(value):
        raise AppValidationError("scenario_id is invalid")
    if value not in _ALLOWED_SCENARIOS:
        raise AppValidationError("Scenario is not allowlisted")


def _validate_plan_id(value: str) -> None:
    if not isinstance(value, str) or not _PLAN_ID_PATTERN.fullmatch(value):
        raise AppValidationError("plan_id is invalid")


def _validate_identity(name: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 128
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError(f"{name} is invalid")


def _validate_trace_id(value: str) -> None:
    if not isinstance(value, str) or not _TRACE_ID_PATTERN.fullmatch(value):
        raise AppValidationError("trace_id is invalid")


def _validate_version(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise AppValidationError("expected_version is invalid")


def _validate_time(name: str, value: datetime) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise AppValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _hash_idempotency_key(value: str | None) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 256
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise AppValidationError("idempotency_key is invalid")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise RuntimeError("Stored remediation timestamp is invalid") from None
    return _validate_time("stored timestamp", parsed)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")


def _failure_code(exc: Exception) -> str:
    message = str(exc)
    if message.startswith("unexpected_status_"):
        return message
    if message in {"response_too_large", "transport_error"}:
        return message
    return "execution_error"
