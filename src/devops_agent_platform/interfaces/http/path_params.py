from typing import Annotated

from fastapi import Path

_STABLE_PATH_SEGMENT_PATTERN = r"^[^\s\x7f]+$"

TenantPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=_STABLE_PATH_SEGMENT_PATTERN,
    ),
]
IncidentPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=_STABLE_PATH_SEGMENT_PATTERN,
    ),
]
WorkflowRunPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=_STABLE_PATH_SEGMENT_PATTERN,
    ),
]
FeedbackPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=_STABLE_PATH_SEGMENT_PATTERN,
    ),
]
OperatorPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=_STABLE_PATH_SEGMENT_PATTERN,
    ),
]
RunbookKeyPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=128,
        pattern=_STABLE_PATH_SEGMENT_PATTERN,
    ),
]
RunbookVersionPath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=_STABLE_PATH_SEGMENT_PATTERN,
    ),
]
WorkspacePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=_STABLE_PATH_SEGMENT_PATTERN,
    ),
]
DatasetReleasePath = Annotated[
    str,
    Path(
        min_length=1,
        max_length=64,
        pattern=_STABLE_PATH_SEGMENT_PATTERN,
    ),
]
"""管理端路径身份字段共享边界，拒绝空白和控制字符。"""
