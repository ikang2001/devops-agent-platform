from typing import Protocol


class IdentifierGeneratorPort(Protocol):
    """应用层生成业务标识所依赖的端口。"""

    def new_alert_id(self) -> str:
        """生成新的告警标识。"""
        ...

    def new_change_event_id(self) -> str:
        """生成新的变更事件标识。"""
        ...

    def new_incident_id(self) -> str:
        """生成新的事故标识。"""
        ...

    def new_event_id(self) -> str:
        """生成新的应用事件标识。"""
        ...

    def new_workflow_run_id(self) -> str:
        """生成新的工作流运行标识。"""
        ...

    def new_permission_grant_id(self) -> str:
        """生成新的权限授权快照标识。"""
        ...

    def new_permission_operation_id(self) -> str:
        """生成新的权限管理操作标识。"""
        ...

    def new_runbook_id(self) -> str:
        """生成新的 Runbook 版本快照标识。"""
        ...

    def new_runbook_operation_id(self) -> str:
        """生成新的 Runbook 管理操作标识。"""
        ...

    def new_workspace_operation_id(self) -> str:
        """生成新的 Workspace 管理操作标识。"""
        ...

    def new_ticket_draft_id(self) -> str:
        """生成新的本地工单草稿标识。"""
        ...

    def new_ticket_submission_id(self) -> str:
        """生成新的外部工单提交请求标识。"""
        ...

    def new_rca_feedback_id(self) -> str:
        """生成新的 RCA 人工反馈标识。"""
        ...

    def new_remediation_plan_id(self) -> str:
        """生成新的受控修复计划标识。"""
        ...
