from fastapi import FastAPI

from devops_agent_platform.interfaces.http.exception_handlers import (
    register_exception_handlers,
)
from devops_agent_platform.interfaces.http.middleware import TraceIdMiddleware
from devops_agent_platform.interfaces.http.routes.alerts import router as alerts_router
from devops_agent_platform.interfaces.http.routes.health import router as health_router
from devops_agent_platform.interfaces.http.routes.incidents import (
    router as incidents_router,
)


def create_app() -> FastAPI:
    """创建并装配 FastAPI 应用。

    bootstrap 层只负责框架级装配，包括中间件、路由和异常处理器。
    业务用例必须留在 application 层，避免启动入口承载业务逻辑。
    """
    app = FastAPI(
        title="DevOps Intelligent Troubleshooting Agent Platform",
        version="0.1.0",
    )
    app.add_middleware(TraceIdMiddleware)
    register_exception_handlers(app)
    app.include_router(health_router)
    app.include_router(alerts_router, prefix="/api/v1")
    app.include_router(incidents_router, prefix="/api/v1")
    return app
