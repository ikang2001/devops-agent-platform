import os

from pydantic import BaseModel, Field


class Settings(BaseModel):
    app_name: str = "MiniShop Fault Lab"
    app_version: str = "0.1.0"
    default_currency: str = "CNY"
    item_unit_price: float = 10.0
    notification_best_effort: bool = True
    tenant_id: str = Field(default="demo", min_length=1, max_length=128)
    agent_url: str = ""
    agent_webhook_secret: str = ""
    agent_request_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    otlp_traces_endpoint: str = ""


settings = Settings(
    tenant_id=os.getenv("MINISHOP_TENANT_ID", "demo"),
    agent_url=os.getenv("MINISHOP_AGENT_URL", ""),
    agent_webhook_secret=os.getenv("MINISHOP_AGENT_WEBHOOK_SECRET", ""),
    agent_request_timeout_seconds=float(
        os.getenv("MINISHOP_AGENT_REQUEST_TIMEOUT_SECONDS", "5")
    ),
    otlp_traces_endpoint=os.getenv("MINISHOP_OTLP_TRACES_ENDPOINT", ""),
)
