"""Database models: registered targets and the jobs run against them."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Target(Base):
    """An Oracle database host the console can run RMAN on."""

    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    hostname: Mapped[str] = mapped_column(String(255))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(64), default="oracle")
    # "key" or "password"
    auth_method: Mapped[str] = mapped_column(String(16), default="key")
    # Required when auth_method is "key"; ignored otherwise.
    ssh_key_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Encrypted at rest. See backend/secrets.py.
    ssh_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Run RMAN as this user via sudo. On OCI the SSH key belongs to opc
    # while the database runs as oracle. Null means run as the SSH user.
    run_as_user: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Null means sudo is configured without a password (NOPASSWD).
    sudo_password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    oracle_home: Mapped[str] = mapped_column(String(512))
    oracle_sid: Mapped[str] = mapped_column(String(64))
    db_unique_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # RMAN behaviour
    channels: Mapped[int] = mapped_column(Integer, default=2)
    compressed: Mapped[bool] = mapped_column(Boolean, default=True)
    # Leave null to write into the Fast Recovery Area.
    backup_format: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Optional recovery catalog connect string, e.g. rcat/pw@catdb.
    # Encrypted at rest because it carries a password.
    catalog_connect_enc: Mapped[str | None] = mapped_column(Text, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    jobs: Mapped[list["Job"]] = relationship(back_populates="target")


class Job(Base):
    """One RMAN invocation on one target."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)

    backup_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)
    tag: Mapped[str] = mapped_column(String(64))

    include_archivelog: Mapped[bool] = mapped_column(Boolean, default=True)
    delete_archivelog: Mapped[bool] = mapped_column(Boolean, default=False)
    backup_controlfile: Mapped[bool] = mapped_column(Boolean, default=True)

    remote_dir: Mapped[str | None] = mapped_column(String(512), nullable=True)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    rman_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_text: Mapped[str] = mapped_column(Text, default="")
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    target: Mapped[Target] = relationship(back_populates="jobs")
