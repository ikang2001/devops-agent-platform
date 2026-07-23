from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """运行时配置占位对象。

    Step 3 只定义配置名称和校验形态。真实密钥管理、数据库连接策略和环境隔离
    会在后续实现步骤补齐。
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="DEVOPS_AGENT_")

    app_env: str = Field(default="local")
    service_name: str = Field(default="devops-agent-platform")
    database_url: str = Field(
        default="postgresql+psycopg://devops:devops@localhost:5432/devops_agent"
    )
    log_level: str = Field(default="INFO")


def get_settings() -> Settings:
    """加载并校验运行时配置。"""
    return Settings()
