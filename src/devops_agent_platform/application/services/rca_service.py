import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256

from devops_agent_platform.application.commands.rca import StartRCACommand
from devops_agent_platform.application.events import OutboxEvent
from devops_agent_platform.domain.enums import WorkflowRunStatus
from devops_agent_platform.domain.exceptions import (
    ConflictError,
    ResourceNotFound,
)
from devops_agent_platform.domain.models.workflow_run import WorkflowRun
from devops_agent_platform.ports.identifiers import IdentifierGeneratorPort
from devops_agent_platform.ports.unit_of_work import UnitOfWorkPort

UnitOfWorkFactory = Callable[[], UnitOfWorkPort]
Clock = Callable[[], datetime]


@dataclass(frozen=True)
class StartRCAResult:
    """启动RCA用例返回给接口层的稳定结果。"""

    workflow_run_id: str
    incident_id: str
    status: str
    trace_id: str
    is_duplicate: bool

    def to_dict(self) -> dict[str, str | bool]:
        """转换为不依赖HTTP框架的数据。"""
        return asdict(self)


class RCAApplicationService:
    """原子创建WorkflowRun并通过Outbox异步触发Agent执行。"""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        identifier_generator: IdentifierGeneratorPort,
        clock: Clock | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._identifier_generator = identifier_generator
        self._clock = clock or (lambda: datetime.now(UTC))

    async def start_rca(self, command: StartRCACommand) -> StartRCAResult:
        """启动RCA；并发唯一约束冲突时只执行一次幂等确认。"""
        idempotency_key_hash = self._hash_text(command.idempotency_key)
        request_hash = self._request_hash(command)
        try:
            return await self._start_once(
                command,
                idempotency_key_hash,
                request_hash,
            )
        except ConflictError:
            recovered = await self._recover_idempotent_result(
                command,
                idempotency_key_hash,
                request_hash,
            )
            if recovered is not None:
                return recovered
            raise

    async def _start_once(
        self,
        command: StartRCACommand,
        idempotency_key_hash: str,
        request_hash: str,
    ) -> StartRCAResult:
        """在单个短事务中完成查重、状态推进、运行记录和Outbox写入。"""
        async with self._unit_of_work_factory() as unit_of_work:
            existing = (
                await unit_of_work.workflow_runs.get_by_idempotency_key_hash(
                    command.tenant_id,
                    idempotency_key_hash,
                )
            )
            if existing is not None:
                return self._existing_result(
                    existing,
                    command,
                    request_hash,
                )

            incident = await unit_of_work.incidents.get_by_id(
                command.incident_id,
                command.tenant_id,
            )
            if incident is None:
                raise ResourceNotFound("Incident not found")

            active = await unit_of_work.workflow_runs.get_active_by_incident(
                command.tenant_id,
                command.incident_id,
            )
            if active is not None:
                raise ConflictError(
                    f"Incident already has an active workflow: "
                    f"{active.workflow_run_id}"
                )

            now = self._clock()
            incident.mark_analyzing(now)
            workflow_run = WorkflowRun(
                workflow_run_id=(
                    self._identifier_generator.new_workflow_run_id()
                ),
                tenant_id=command.tenant_id,
                incident_id=command.incident_id,
                operator_id=command.operator_id,
                idempotency_key_hash=idempotency_key_hash,
                request_hash=request_hash,
                trace_id=command.trace_id,
                status=WorkflowRunStatus.PENDING,
                created_at=now,
                updated_at=now,
                started_at=None,
                ended_at=None,
                step_count=0,
            )
            await unit_of_work.incidents.save(incident)
            await unit_of_work.workflow_runs.save(workflow_run)
            await unit_of_work.outbox.add(
                self._build_requested_event(workflow_run, now)
            )
            await unit_of_work.commit()
            return self._result(
                workflow_run,
                trace_id=command.trace_id,
                is_duplicate=False,
            )

    async def _recover_idempotent_result(
        self,
        command: StartRCACommand,
        idempotency_key_hash: str,
        request_hash: str,
    ) -> StartRCAResult | None:
        """提交竞态后重新读取，区分同请求重放与真实并发冲突。"""
        async with self._unit_of_work_factory() as unit_of_work:
            existing = (
                await unit_of_work.workflow_runs.get_by_idempotency_key_hash(
                    command.tenant_id,
                    idempotency_key_hash,
                )
            )
            if existing is None:
                return None
            return self._existing_result(
                existing,
                command,
                request_hash,
            )

    def _existing_result(
        self,
        existing: WorkflowRun,
        command: StartRCACommand,
        request_hash: str,
    ) -> StartRCAResult:
        """验证幂等键没有被复用于不同业务请求。"""
        if existing.request_hash != request_hash:
            raise ConflictError(
                "Idempotency key was already used for another RCA request"
            )
        if existing.incident_id != command.incident_id:
            raise ConflictError(
                "Idempotency key was already used for another incident"
            )
        return self._result(
            existing,
            trace_id=command.trace_id,
            is_duplicate=True,
        )

    def _build_requested_event(
        self,
        workflow_run: WorkflowRun,
        occurred_at: datetime,
    ) -> OutboxEvent:
        """构造提交后由Agent消费者处理的版本化事件。"""
        return OutboxEvent(
            event_id=self._identifier_generator.new_event_id(),
            tenant_id=workflow_run.tenant_id,
            aggregate_type="WorkflowRun",
            aggregate_id=workflow_run.workflow_run_id,
            event_type="rca.requested",
            schema_version=1,
            payload={
                "workflow_run_id": workflow_run.workflow_run_id,
                "incident_id": workflow_run.incident_id,
                "tenant_id": workflow_run.tenant_id,
                "operator_id": workflow_run.operator_id,
                "requested_at": occurred_at.isoformat(),
            },
            occurred_at=occurred_at,
            trace_id=workflow_run.trace_id,
        )

    @staticmethod
    def _result(
        workflow_run: WorkflowRun,
        trace_id: str,
        is_duplicate: bool,
    ) -> StartRCAResult:
        """构造不泄漏幂等哈希和内部审计字段的返回结果。"""
        return StartRCAResult(
            workflow_run_id=workflow_run.workflow_run_id,
            incident_id=workflow_run.incident_id,
            status=workflow_run.status.value,
            trace_id=trace_id,
            is_duplicate=is_duplicate,
        )

    @staticmethod
    def _hash_text(value: str) -> str:
        """生成不可逆的幂等键摘要，数据库不保存调用方原文。"""
        return sha256(value.encode()).hexdigest()

    @staticmethod
    def _request_hash(command: StartRCACommand) -> str:
        """为幂等语义生成字段顺序稳定的请求指纹。"""
        encoded = json.dumps(
            {
                "incident_id": command.incident_id,
                "operator_id": command.operator_id,
                "tenant_id": command.tenant_id,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return sha256(encoded).hexdigest()
