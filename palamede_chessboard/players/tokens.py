"""API tokens of the player registry (#34).

Format : ``plm_<id>_<secret>`` — ``id`` is 8 hex chars, public, used to
find the token ; ``secret`` is 32 random bytes (urlsafe base64). Only
``sha256(secret)`` is stored : for a 256-bit random secret a slow hash
(argon2, scrypt) adds nothing, and a fast one keeps every request cheap.
Comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

TOKEN_PREFIX = "plm"
_TOKEN_RE = re.compile(r"^plm_(?P<id>[0-9a-f]{8})_(?P<secret>[A-Za-z0-9_-]{43})$")


def new_token_id(taken: set[str]) -> str:
    """A fresh 8-hex token id, unique among ``taken``."""
    while True:
        token_id = secrets.token_hex(4)
        if token_id not in taken:
            return token_id


def new_secret() -> str:
    """32 random bytes, urlsafe base64 without padding (43 chars)."""
    return secrets.token_urlsafe(32)


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def format_token(token_id: str, secret: str) -> str:
    return f"{TOKEN_PREFIX}_{token_id}_{secret}"


def parse_token(token: object) -> tuple[str, str] | None:
    """``(id, secret)`` of a well-formed token, else ``None``."""
    if not isinstance(token, str):
        return None
    match = _TOKEN_RE.match(token.strip())
    return (match.group("id"), match.group("secret")) if match else None


def matches(secret: str, stored_digest: str) -> bool:
    return hmac.compare_digest(digest(secret), stored_digest)
