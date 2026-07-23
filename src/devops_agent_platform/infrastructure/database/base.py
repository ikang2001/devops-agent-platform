from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# 所有 ORM 模型继承同一个 Base，共享主键、索引、唯一约束、外键和检查约束的
# 命名规则，避免不同开发者或数据库环境生成不一致的名称。
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类，统一约束和索引命名规则。

    稳定命名能让 Alembic 在不同数据库环境生成一致迁移，也便于线上根据约束名
    快速定位唯一键、外键或检查约束故障。
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
