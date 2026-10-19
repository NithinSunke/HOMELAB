"""Request and response shapes.

Validation here is the security boundary: every value that reaches a remote
shell command or an RMAN script is checked against a conservative pattern
before it is accepted.
"""
from __future__ import annotations

import datetime as dt
import re

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

BACKUP_TYPES = ("LEVEL0", "LEVEL1_CUM", "LEVEL1_DIFF", "ARCHIVELOG")

# Deliberately narrow. Anything a shell or RMAN could reinterpret is rejected
# rather than escaped, because these values are set once by an administrator.
_PATH_RE = re.compile(r"^[A-Za-z0-9._/+\-]+$")
# ssh_key_path is opened by the app on its own machine and never reaches a
# remote shell, so Windows drive letters, backslashes and spaces are fine.
# Shell metacharacters are still refused as a matter of habit.
_LOCAL_PATH_RE = re.compile(r"^[A-Za-z0-9 ._/\\:+\-]+$")
_SID_RE = re.compile(r"^[A-Za-z0-9_$]+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9._\-]+$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.\-]+$")


class TargetIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    hostname: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(default="oracle", max_length=64)
    auth_method: str = Field(default="key")
    # Required when auth_method is "key".
    ssh_key_path: str | None = Field(default=None, max_length=512)
    # Required when auth_method is "password". Write only; never returned.
    ssh_password: str | None = Field(default=None, max_length=512)

    run_as_user: str | None = Field(default=None, max_length=64)
    # Leave empty when sudo is configured NOPASSWD. Write only.
    sudo_password: str | None = Field(default=None, max_length=512)

    oracle_home: str = Field(min_length=1, max_length=512)
    oracle_sid: str = Field(min_length=1, max_length=64)
    db_unique_name: str | None = Field(default=None, max_length=64)

    channels: int = Field(default=2, ge=1, le=32)
    compressed: bool = True
    backup_format: str | None = Field(default=None, max_length=512)
    catalog_connect: str | None = Field(default=None, max_length=255)
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def _name_ok(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError("name may contain letters, digits, dot, dash, underscore")
        return v

    @field_validator("hostname")
    @classmethod
    def _host_ok(cls, v: str) -> str:
        if not _HOST_RE.match(v):
            raise ValueError("hostname must be a plain host name or IP address")
        return v

    @field_validator("ssh_user")
    @classmethod
    def _user_ok(cls, v: str) -> str:
        if not _NAME_RE.match(v):
            raise ValueError("ssh_user may contain letters, digits, dot, dash, underscore")
        return v

    @field_validator("run_as_user")
    @classmethod
    def _run_as_ok(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not _NAME_RE.match(v):
            raise ValueError(
                "run_as_user may contain letters, digits, dot, dash, underscore"
            )
        return v

    @field_validator("oracle_home")
    @classmethod
    def _path_ok(cls, v: str) -> str:
        """ORACLE_HOME is interpolated into a remote shell, so keep it strict."""
        if not _PATH_RE.match(v):
            raise ValueError("path contains characters that are not allowed")
        return v

    @field_validator("ssh_key_path")
    @classmethod
    def _local_path_ok(cls, v: str | None) -> str | None:
        """Path on the app server. Windows paths are accepted here."""
        if v is None or v == "":
            return None
        if not _LOCAL_PATH_RE.match(v):
            raise ValueError("ssh_key_path contains characters that are not allowed")
        return v

    @field_validator("auth_method")
    @classmethod
    def _auth_ok(cls, v: str) -> str:
        if v not in ("key", "password"):
            raise ValueError("auth_method must be 'key' or 'password'")
        return v

    @model_validator(mode="after")
    def _credentials_present(self) -> "TargetIn":
        """Each authentication method needs its own credential."""
        if self.auth_method == "key" and not self.ssh_key_path:
            raise ValueError("ssh_key_path is required when auth_method is 'key'")
        if self.auth_method == "password" and not self.ssh_password:
            raise ValueError("ssh_password is required when auth_method is 'password'")
        if self.sudo_password and not self.run_as_user:
            raise ValueError("sudo_password only applies when run_as_user is set")
        return self

    @field_validator("oracle_sid")
    @classmethod
    def _sid_ok(cls, v: str) -> str:
        if not _SID_RE.match(v):
            raise ValueError("ORACLE_SID may contain letters, digits, underscore, dollar")
        return v

    @field_validator("db_unique_name")
    @classmethod
    def _dbun_ok(cls, v: str | None) -> str | None:
        if v and not _SID_RE.match(v):
            raise ValueError("db_unique_name may contain letters, digits, underscore, dollar")
        return v

    @field_validator("backup_format")
    @classmethod
    def _format_ok(cls, v: str | None) -> str | None:
        if v is None:
            return v
        # RMAN format substitution variables use %, which is safe inside a
        # quoted RMAN string but must not carry shell metacharacters.
        if not re.match(r"^[A-Za-z0-9._/%+\-]+$", v):
            raise ValueError("backup_format contains characters that are not allowed")
        return v


class TargetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    hostname: str
    ssh_port: int
    ssh_user: str
    auth_method: str
    ssh_key_path: str | None
    has_ssh_password: bool = False
    run_as_user: str | None
    has_sudo_password: bool = False
    oracle_home: str
    oracle_sid: str
    db_unique_name: str | None
    channels: int
    compressed: bool
    backup_format: str | None
    has_catalog: bool = False
    notes: str | None
    created_at: dt.datetime


class JobIn(BaseModel):
    target_id: int
    backup_type: str
    include_archivelog: bool = True
    # Off by default: deleting archive logs is not reversible.
    delete_archivelog: bool = False
    backup_controlfile: bool = True

    @field_validator("backup_type")
    @classmethod
    def _type_ok(cls, v: str) -> str:
        if v not in BACKUP_TYPES:
            raise ValueError(f"backup_type must be one of {', '.join(BACKUP_TYPES)}")
        return v


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_id: int
    backup_type: str
    status: str
    tag: str
    include_archivelog: bool
    delete_archivelog: bool
    backup_controlfile: bool
    exit_code: int | None
    error_summary: str | None
    created_at: dt.datetime
    started_at: dt.datetime | None
    finished_at: dt.datetime | None


class JobDetail(JobOut):
    rman_script: str | None
    log_text: str


class TestResult(BaseModel):
    ok: bool
    detail: str
