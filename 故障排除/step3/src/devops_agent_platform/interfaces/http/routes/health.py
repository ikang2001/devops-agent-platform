from fastapi import APIRouter, Request

from devops_agent_platform.interfaces.http.responses import (
    ResponseEnvelope,
    get_trace_id,
    success_response,
)

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=ResponseEnvelope)
async def healthz(request: Request) -> dict:
    """返回服务健康状态。

    该接口在 Step 3 阶段就允许真实可用，用来验证启动装配、
    统一响应外壳和 trace_id 中间件是否工作正常。
    """
    trace_id = get_trace_id(request)
    return success_response(
        data={"status": "ok", "service": "devops-agent-platform"},
        trace_id=trace_id,
    )
