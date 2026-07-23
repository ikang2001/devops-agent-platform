from typing import Annotated

from fastapi import APIRouter, Depends, Request

from devops_agent_platform.application.exceptions import RuntimeUnavailableError
from devops_agent_platform.bootstrap.dependencies import get_application_runtime
from devops_agent_platform.bootstrap.runtime import ApplicationRuntime
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["health"])
RuntimeDep = Annotated[ApplicationRuntime, Depends(get_application_runtime)]


@router.get("/healthz", response_model=ResponseEnvelope)
async def healthz(request: Request) -> dict:
    """返回不访问外部依赖的进程存活状态。

    liveness失败通常会触发容器重启，因此不能把数据库、Kafka等短暂故障纳入
    本接口，否则会在依赖故障期间造成无效重启风暴。
    """
    trace_id = get_trace_id(request)
    return success_response(
        data={"status": "ok", "service": "devops-agent-platform"},
        trace_id=trace_id,
    )


@router.get("/readyz", response_model=ResponseEnvelope)
async def readyz(request: Request, runtime: RuntimeDep) -> dict:
    """检查实例是否具备安全接收业务流量的必要条件。"""
    snapshot = await runtime.check_readiness()
    if not snapshot.ready:
        unavailable = ", ".join(snapshot.unavailable_components)
        raise RuntimeUnavailableError(f"Components not ready: {unavailable}")

    return success_response(
        data=snapshot.to_dict(),
        trace_id=get_trace_id(request),
    )
