"""Bearer token check.

One shared token is enough for a tool on an internal network, but it is not
per-user authentication. Put this behind your SSO or a reverse proxy that
authenticates before anyone outside your team can reach it.
"""
from fastapi import Header, HTTPException, status

from .config import API_TOKEN


def require_token(authorization: str = Header(default="")) -> None:
    if not API_TOKEN:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "RMAN_CONSOLE_TOKEN is not set; refusing to serve unauthenticated.",
        )
    expected = f"Bearer {API_TOKEN}"
    # Length-constant comparison is overkill for a LAN tool but costs nothing.
    if len(authorization) != len(expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad token")
    if sum(a != b for a, b in zip(authorization, expected)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad token")
