"""外部工单系统提交适配器。"""

from devops_agent_platform.infrastructure.ticketing.http_json import (
    HttpJsonTicketingGateway,
    HttpJsonTicketingGatewayConfig,
)

__all__ = [
    "HttpJsonTicketingGateway",
    "HttpJsonTicketingGatewayConfig",
]
