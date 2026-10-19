"""SQLAlchemy engine and session factory.

SQLite is enough here: job volume is low and rows are small. Sessions are
short-lived, so the brief blocking inside async code is not worth avoiding.
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}, future=True
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401  (registers the mappings)

    Base.metadata.create_all(engine)
