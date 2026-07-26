from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter(tags=["console"])
_CONSOLE_DIRECTORY = (
    Path(__file__).resolve().parent.parent / "static" / "ops-console"
)
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "base-uri 'none'; "
        "connect-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
}


def _console_file(filename: str, media_type: str) -> FileResponse:
    """返回仓库内固定控制台资源，不接受调用方提供的文件路径。"""
    return FileResponse(
        _CONSOLE_DIRECTORY / filename,
        media_type=media_type,
        headers=_SECURITY_HEADERS,
    )


@router.get("/console", include_in_schema=False)
@router.get("/console/", include_in_schema=False)
async def console_index() -> FileResponse:
    """提供与管理 API 同源部署的运维控制台。"""
    return _console_file("index.html", "text/html")


@router.get("/console/assets/styles.css", include_in_schema=False)
async def console_styles() -> FileResponse:
    """提供控制台样式；显式路由避免静态目录遍历面。"""
    return _console_file("styles.css", "text/css")


@router.get("/console/assets/app.js", include_in_schema=False)
async def console_script() -> FileResponse:
    """提供控制台脚本；脚本内不包含环境凭据。"""
    return _console_file("app.js", "application/javascript")
