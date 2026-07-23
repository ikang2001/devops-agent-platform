from devops_agent_platform.domain.exceptions import AppValidationError

_MAX_WORKER_ID_LENGTH = 128


def validate_worker_id(worker_id: object) -> str:
    """校验后台执行者身份，避免污染租约、日志和健康快照。"""
    if not isinstance(worker_id, str):
        raise AppValidationError("worker_id must be a string")
    if not 1 <= len(worker_id) <= _MAX_WORKER_ID_LENGTH:
        raise AppValidationError(
            "worker_id length must be between 1 and 128"
        )
    if worker_id != worker_id.strip():
        raise AppValidationError(
            "worker_id must not contain surrounding whitespace"
        )
    if any(
        ord(character) < 32 or ord(character) == 127
        for character in worker_id
    ):
        raise AppValidationError(
            "worker_id must not contain control characters"
        )
    if any(character.isspace() for character in worker_id):
        raise AppValidationError("worker_id must not contain whitespace")
    return worker_id
