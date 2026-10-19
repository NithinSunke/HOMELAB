"""SQLAlchemy engine and session factory.

SQLite is enough here: job volume is low and rows are small. Sessions are
short-lived, so the brief blocking inside async code is not worth avoiding.
"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL, DB_PATH

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}, future=True
)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401  (registers the mappings)

    parent = Path(DB_PATH).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError(
            f"Cannot create the folder {parent} for the database file.\n"
            f"Check RMAN_CONSOLE_DB in your .env, or comment it out to keep "
            f"the database beside the project.\nUnderlying error: {exc}"
        ) from exc

    try:
        Base.metadata.create_all(engine)
        _add_missing_columns()
    except OperationalError as exc:
        raise RuntimeError(
            f"Cannot open the database file at {DB_PATH}.\n"
            f"Check RMAN_CONSOLE_DB in your .env, or comment it out to keep "
            f"the database beside the project.\nUnderlying error: {exc}"
        ) from exc


def _add_missing_columns() -> None:
    """Tiny forward migration for databases created by an earlier version.

    Enough for a single-file SQLite tool; anything more would call for Alembic.
    """
    from sqlalchemy import text

    additions = {
        "targets": {
            "run_as_user": "VARCHAR(64)",
            "auth_method": "VARCHAR(16) DEFAULT 'key'",
            "ssh_password_enc": "TEXT",
            "sudo_password_enc": "TEXT",
            "catalog_connect_enc": "TEXT",
        },
    }
    with engine.begin() as conn:
        for table, columns in additions.items():
            existing = {
                row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
