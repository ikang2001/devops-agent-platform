from typing import Annotated

from fastapi import APIRouter, Depends, Request

from devops_agent_platform.application.services.rca_service import RCAApplicationService
from devops_agent_platform.bootstrap.dependencies import get_rca_application_service
from devops_agent_platform.interfaces.http.dto import StartRCARequest
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["incidents"])
RCAServiceDep = Annotated[
    RCAApplicationService,
    Depends(get_rca_application_service),
]


@router.post("/incidents/{incident_id}/rca", response_model=ResponseEnvelope)
async def start_rca(
    incident_id: str,
    payload: StartRCARequest,
    request: Request,
    service: RCAServiceDep,
) -> dict:
    """为指定事故启动 RCA 分析。

    Step 3 只暴露 HTTP 契约。真实业务路径尚未实现，会通过全局异常处理器
    返回 501，明确告诉调用方该能力仍处于骨架阶段。
    """
    trace_id = get_trace_id(request)
    command = payload.to_command(incident_id=incident_id, trace_id=trace_id)
    result = await service.start_rca(command)
    return success_response(data=result, trace_id=trace_id)
