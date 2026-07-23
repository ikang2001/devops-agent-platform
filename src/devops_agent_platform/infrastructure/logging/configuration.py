import logging
import sys

from devops_agent_platform.infrastructure.logging.formatter import JsonLogFormatter

_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def configure_logging(
    *,
    service_name: str,
    environment: str,
    level: str,
) -> None:
    """集中配置根日志器，保证应用模块使用一致的JSON契约。"""
    normalized_level = level.upper()
    if normalized_level not in _LOG_LEVELS:
        raise ValueError(f"Unsupported log level: {level}")

    handler = logging.StreamHandler(sys.stdout)
    handler._devops_agent_owned = True  # type: ignore[attr-defined]
    handler.setFormatter(
        JsonLogFormatter(
            service_name=service_name,
            environment=environment,
        )
    )

    root_logger = logging.getLogger()
    for existing_handler in list(root_logger.handlers):
        if getattr(existing_handler, "_devops_agent_owned", False):
            root_logger.removeHandler(existing_handler)
    root_logger.addHandler(handler)
    root_logger.setLevel(_LOG_LEVELS[normalized_level])

    # HTTP访问日志由应用中间件统一输出，避免Uvicorn产生字段不同的重复记录。
    logging.getLogger("uvicorn.access").disabled = True
    # HTTP客户端默认会记录完整URL，查询参数可能包含令牌或临时签名。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
