"""Encryption for stored credentials.

Passwords and catalog connect strings are encrypted with Fernet before they
reach the database, so the SQLite file and any backup of it do not carry
readable credentials.

Be clear about what this does and does not buy you. The key sits on the same
machine as the database, so anyone who can read the app server can decrypt.
It protects against a leaked database file, a shared backup, or a glance over
a shoulder. It is not a substitute for restricting who can reach the host.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import DB_PATH

log = logging.getLogger("rman_console.secrets")


def _key_file() -> Path:
    return Path(DB_PATH).with_name("secret.key")


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    env = os.getenv("RMAN_CONSOLE_SECRET_KEY", "").strip()
    if env:
        return Fernet(env.encode())

    path = _key_file()
    if path.exists():
        return Fernet(path.read_bytes().strip())

    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    try:
        path.chmod(0o600)
    except OSError:  # Windows ACLs do not map onto chmod
        pass
    log.warning(
        "Generated a new encryption key at %s. Keep it with the database: "
        "losing it means stored passwords cannot be decrypted.", path
    )
    return Fernet(key)


def encrypt(plain: str | None) -> str | None:
    if plain is None or plain == "":
        return None
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if token is None or token == "":
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "A stored credential could not be decrypted. The encryption key "
            f"({_key_file()} or RMAN_CONSOLE_SECRET_KEY) does not match the "
            "one used when it was saved. Re-enter the credential on the "
            "target, or restore the original key."
        ) from exc
