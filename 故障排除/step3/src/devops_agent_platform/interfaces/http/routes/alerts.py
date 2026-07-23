from typing import Annotated

from fastapi import APIRouter, Depends, Request

from devops_agent_platform.application.services.alert_service import (
    AlertApplicationService,
)
from devops_agent_platform.bootstrap.dependencies import get_alert_application_service
from devops_agent_platform.interfaces.http.dto import AlertWebhookRequest
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["alerts"])
AlertServiceDep = Annotated[
    AlertApplicationService,
    Depends(get_alert_application_service),
]


@router.post("/alerts", response_model=ResponseEnvelope)
async def receive_alert(
    payload: AlertWebhookRequest,
    request: Request,
    service: AlertServiceDep,
) -> dict:
    """接收一条外部告警 Webhook。

    Step 3 只验证路由装配和 DTO 到 Command 的转换。
    应用服务会故意抛出 NotImplementedInSkeleton，避免伪造业务成功路径。
    """
    trace_id = get_trace_id(request)
    command = payload.to_command(trace_id=trace_id)
    result = await service.receive_alert(command)
    return success_response(data=result, trace_id=trace_id)
