from uuid import uuid4


class UUIDIdentifierGenerator:
    """使用随机 UUID 生成不依赖数据库序列的业务标识。"""

    def new_alert_id(self) -> str:
        """生成带类型前缀的告警标识，方便日志和人工排障识别。"""
        return f"alt_{uuid4().hex}"

    def new_change_event_id(self) -> str:
        """生成带类型前缀的变更事件标识。"""
        return f"chg_{uuid4().hex}"

    def new_incident_id(self) -> str:
        """生成带类型前缀的事故标识。"""
        return f"inc_{uuid4().hex}"

    def new_event_id(self) -> str:
        """生成带类型前缀的应用事件标识。"""
        return f"evt_{uuid4().hex}"

    def new_workflow_run_id(self) -> str:
        """生成带类型前缀的工作流运行标识。"""
        return f"wfr_{uuid4().hex}"

    def new_permission_grant_id(self) -> str:
        """生成带类型前缀的权限授权快照标识。"""
        return f"pgr_{uuid4().hex}"

    def new_permission_operation_id(self) -> str:
        """生成带类型前缀的权限管理操作标识。"""
        return f"pop_{uuid4().hex}"

    def new_runbook_id(self) -> str:
        """生成带类型前缀的 Runbook 版本快照标识。"""
        return f"rbk_{uuid4().hex}"

    def new_runbook_operation_id(self) -> str:
        """生成带类型前缀的 Runbook 管理操作标识。"""
        return f"rop_{uuid4().hex}"

    def new_ticket_draft_id(self) -> str:
        """生成带类型前缀的本地工单草稿标识。"""
        return f"tdf_{uuid4().hex}"

    def new_ticket_submission_id(self) -> str:
        """生成带类型前缀的外部工单提交请求标识。"""
        return f"tsb_{uuid4().hex}"

    def new_rca_feedback_id(self) -> str:
        """生成带类型前缀的 RCA 人工反馈标识。"""
        return f"rcf_{uuid4().hex}"

    def new_remediation_plan_id(self) -> str:
        """生成带类型前缀的受控修复计划标识。"""
        return f"rmp_{uuid4().hex}"
