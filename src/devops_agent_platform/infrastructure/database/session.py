# 它负责创建“连接数据库的工具”，以及创建“每次操作数据库时用的 Session 工厂”。
# 1. 根据配置创建数据库引擎 Engine
# 2. 根据 Engine 创建 Session 工厂
# 3. 后续每个请求用 Session 工厂创建自己的数据库 Session
# 4. Repository 拿着 Session 去保存/查询数据库
# create_engine() 负责连接数据库的大管家；
# create_session_factory() 负责生产每次请求用的数据库操作对象；
# 真正写数据库的 Repository 要靠这里创建出来的 Session。
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from devops_agent_platform.infrastructure.config.settings import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    """创建 SQLAlchemy 异步引擎。

    ``pool_pre_ping`` 会在借出连接前检查连接有效性，降低数据库重启、网络闪断后
    复用失效连接的概率。引擎应在应用生命周期内保持单例，并在停机阶段显式释放。
    """
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """基于共享引擎创建异步 Session 工厂。

    关闭 ``expire_on_commit``，避免提交后读取已加载字段时触发隐式 IO。Session
    必须按请求或消息消费作用域创建，不能跨并发任务共享。
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
