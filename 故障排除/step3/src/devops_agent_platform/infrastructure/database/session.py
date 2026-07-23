from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from devops_agent_platform.infrastructure.config.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """创建 SQLAlchemy 异步引擎。

    Step 3 不主动打开连接，也不定义 ORM 模型。该函数用于提前固定引擎创建入口，
    方便后续仓储适配器统一复用。
    """
    return create_async_engine(settings.database_url)
