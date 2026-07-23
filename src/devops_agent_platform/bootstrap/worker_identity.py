from hashlib import sha256

_MAX_DERIVED_ID_LENGTH = 128
_DIGEST_LENGTH = 12


def derive_suffixed_id(
    base_id: str,
    suffix: str,
    *,
    max_length: int = _MAX_DERIVED_ID_LENGTH,
) -> str:
    """从基础身份派生带后缀的稳定标识，过长时加入哈希避免碰撞。"""
    candidate = f"{base_id}-{suffix}"
    if len(candidate) <= max_length:
        return candidate
    digest = sha256(candidate.encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    prefix_length = max_length - len(suffix) - len(digest) - 2
    if prefix_length < 1:
        raise ValueError("suffix leaves no room for a stable identity prefix")
    prefix = base_id[:prefix_length]
    return f"{prefix}-{digest}-{suffix}"


def derive_worker_id(client_id: str, suffix: str = "worker") -> str:
    """从客户端标识派生稳定 Worker ID，过长时保留哈希避免碰撞。"""
    return derive_suffixed_id(client_id, suffix)
