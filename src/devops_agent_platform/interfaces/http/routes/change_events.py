from typing import Annotated

from fastapi import APIRouter, Depends, Request

from devops_agent_platform.application.services.change_event_service import (
    ChangeEventApplicationService,
)
from devops_agent_platform.bootstrap.dependencies import (
    get_change_event_application_service,
    verify_alert_webhook,
)
from devops_agent_platform.interfaces.http.dto import ChangeEventWebhookRequest
from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["change-events"])
ChangeEventServiceDep = Annotated[
    ChangeEventApplicationService,
    Depends(get_change_event_application_service),
]


@router.post(
    "/change-events",
    response_model=ResponseEnvelope,
    dependencies=[Depends(verify_alert_webhook)],
)
async def receive_change_event(
    payload: ChangeEventWebhookRequest,
    request: Request,
    service: ChangeEventServiceDep,
) -> dict:
    """验证机器签名后接收一条外部变更事实。"""
    trace_id = get_trace_id(request)
    result = await service.receive_change_event(payload.to_command(trace_id))
    return success_response(data=result.to_dict(), trace_id=trace_id)
