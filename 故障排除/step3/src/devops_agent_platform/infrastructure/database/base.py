from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """为后续 ORM 模型预留的 SQLAlchemy 声明式基类。"""
