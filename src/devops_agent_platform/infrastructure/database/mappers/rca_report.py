import json

from devops_agent_platform.domain.enums import RCAConclusionStatus
from devops_agent_platform.domain.exceptions import AppValidationError
from devops_agent_platform.domain.models.rca_report import RCAReport
from devops_agent_platform.infrastructure.database.models.rca_report import (
    RCAReportRecord,
)


class RCAReportMapper:
    """在 RCAReport 领域对象与 ORM 快照之间显式转换。"""

    @staticmethod
    def to_record(report: RCAReport) -> RCAReportRecord:
        """复验领域约束后转换，防止被绕过的不变量进入数据库。"""
        if not isinstance(report, RCAReport):
            raise AppValidationError("report must be an RCAReport")
        report.validate()
        return RCAReportRecord(
            report_id=report.report_id,
            tenant_id=report.tenant_id,
            incident_id=report.incident_id,
            workflow_run_id=report.workflow_run_id,
            execution_attempt=report.execution_attempt,
            conclusion_status=report.conclusion_status.value,
            title=report.title,
            summary=report.summary,
            confidence=report.confidence,
            evidence_ids_json=_encode_json(report.evidence_ids),
            evidence_type_counts_json=_encode_json(
                report.evidence_type_counts
            ),
            recommendations_json=_encode_json(report.recommendations),
            generator_name=report.generator_name,
            generator_version=report.generator_version,
            generated_at=report.generated_at,
        )

    @staticmethod
    def to_domain(record: RCAReportRecord) -> RCAReport:
        """恢复报告；只兼容转义历史展示文本，身份污染仍拒绝。"""
        evidence_ids = _decode_json_list(
            record.evidence_ids_json,
            "evidence_ids_json",
        )
        type_counts = _decode_type_counts(
            record.evidence_type_counts_json,
            "evidence_type_counts_json",
        )
        recommendations = _decode_json_list(
            record.recommendations_json,
            "recommendations_json",
        )
        return RCAReport(
            report_id=record.report_id,
            tenant_id=record.tenant_id,
            incident_id=record.incident_id,
            workflow_run_id=record.workflow_run_id,
            execution_attempt=record.execution_attempt,
            conclusion_status=RCAConclusionStatus(
                record.conclusion_status
            ),
            title=_escape_legacy_display_text(
                record.title,
                maximum=256,
                multiline=False,
            ),
            summary=_escape_legacy_display_text(
                record.summary,
                maximum=4096,
                multiline=True,
            ),
            confidence=record.confidence,
            evidence_ids=tuple(evidence_ids),
            evidence_type_counts=tuple(type_counts),
            recommendations=tuple(
                _escape_legacy_display_text(
                    item,
                    maximum=1024,
                    multiline=False,
                )
                for item in recommendations
            ),
            generator_name=record.generator_name,
            generator_version=record.generator_version,
            generated_at=record.generated_at,
        )


def _escape_legacy_display_text(
    value: object,
    *,
    maximum: int,
    multiline: bool,
) -> str:
    """把旧展示文本中的控制字符转成完整且有界的可见转义。"""
    if not isinstance(value, str):
        raise AppValidationError("stored report display text is invalid")
    result: list[str] = []
    length = 0
    for character in value:
        if character == "\n" and multiline:
            token = character
        elif ord(character) < 32 or ord(character) == 127:
            token = f"\\u{ord(character):04x}"
        else:
            token = character
        if length + len(token) > maximum:
            break
        result.append(token)
        length += len(token)
    return "".join(result).strip()


def _encode_json(value: object) -> str:
    """使用稳定紧凑格式保存有界报告结构。"""
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _decode_json_list(value: str, field_name: str) -> list:
    """解码数据库 JSON 文本并要求顶层为数组。"""
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise AppValidationError(f"{field_name} is invalid JSON") from exc
    if not isinstance(decoded, list):
        raise AppValidationError(f"{field_name} must contain a JSON array")
    return decoded


def _decode_type_counts(
    value: str,
    field_name: str,
) -> list[tuple[str, int]]:
    """解码并校验证据类型计数二维数组。"""
    decoded = _decode_json_list(value, field_name)
    result: list[tuple[str, int]] = []
    for item in decoded:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], str)
            or isinstance(item[1], bool)
            or not isinstance(item[1], int)
        ):
            raise AppValidationError(f"{field_name} item is invalid")
        result.append((item[0], item[1]))
    return result
