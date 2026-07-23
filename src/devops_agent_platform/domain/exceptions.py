class AppException(Exception):
    """应用异常基类，会被映射为统一 HTTP 响应外壳。"""

    code = "APP_ERROR"
    status_code = 500
    default_message = "Application error"

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class AppValidationError(AppException):
    """应用层或领域层校验失败时抛出。"""

    code = "VALIDATION_ERROR"
    status_code = 400
    default_message = "Validation failed"


class PermissionDenied(AppException):
    """调用方无权执行当前操作时抛出。"""

    code = "PERMISSION_DENIED"
    status_code = 403
    default_message = "Permission denied"


class AuthenticationRequired(AppException):
    """请求没有携带可验证的身份凭证。"""

    code = "AUTHENTICATION_REQUIRED"
    status_code = 401
    default_message = "Authentication is required"
    authenticate_scheme = "Bearer"


class WebhookAuthenticationRequired(AuthenticationRequired):
    """外部Webhook缺少有效机器签名。"""

    authenticate_scheme = "HMAC-SHA256"


class ResourceNotFound(AppException):
    """请求的资源不存在时抛出。"""

    code = "RESOURCE_NOT_FOUND"
    status_code = 404
    default_message = "Resource not found"


class ConflictError(AppException):
    """请求与当前资源状态冲突时抛出。"""

    code = "CONFLICT"
    status_code = 409
    default_message = "Resource state conflict"


class NotImplementedInSkeleton(AppException):
    """Skeleton 模式主动拒绝需要生产依赖的业务路径。"""

    code = "NOT_IMPLEMENTED"
    status_code = 501
    default_message = "Business capability is unavailable in skeleton mode"
