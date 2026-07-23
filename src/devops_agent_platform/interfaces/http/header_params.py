from typing import Annotated

from fastapi import Header

_STABLE_HEADER_ID_PATTERN = r"^[^\s\x7f]+$"

IdempotencyKeyHeader = Annotated[
    str,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        pattern=_STABLE_HEADER_ID_PATTERN,
    ),
]
"""所有写接口共享的幂等键头部边界，拒绝空白和控制字符。"""
