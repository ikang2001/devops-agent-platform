from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from devops_agent_platform.domain.exceptions import AppException
from devops_agent_platform.interfaces.http.responses import error_response, get_trace_id


def register_exception_handlers(app: FastAPI) -> None:
    """注册框架异常和应用异常的统一 HTTP 处理器。"""

    @app.exception_handler(AppException)
    async def handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
        trace_id = get_trace_id(request)
        return JSONResponse(
            status_code=exc.status_code,
            content=error_response(exc.code, exc.message, trace_id),
            headers={"X-Trace-Id": trace_id},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        trace_id = get_trace_id(request)
        return JSONResponse(
            status_code=422,
            content=error_response(
                "REQUEST_VALIDATION_ERROR",
                "Request payload validation failed",
                trace_id,
            ),
            headers={"X-Trace-Id": trace_id},
        )
