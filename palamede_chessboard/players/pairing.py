"""Pairing a board to a player by a short code (#351 §4, #227, RFC 8628).

1. The board asks :func:`Pairings.start` (``POST /api/device/code``) : it
   gets a secret ``device_code`` and a short ``user_code`` to show.
2. The player, signed in to the viewer, types the ``user_code`` on
   ``/appairer``, sees the board's announced name and the hour of the
   request, and confirms (:func:`Pairings.approve`) or refuses.
3. The board polls :func:`Pairings.poll` (``POST /api/device/token``)
   with its ``device_code`` : ``authorization_pending`` until the player
   answers, then the ``device`` token, **once**.

Rules (design #351 §4, review Admin) :

* a ``user_code`` is 8 characters ``XXXX-XXXX`` of an alphabet without
  0/O nor 1/I, lives :data:`EXPIRES_IN` seconds, and serves once ;
* a poll sooner than the interval gets ``slow_down`` and the interval
  grows by 5 s, as RFC 8628 §3.5 says ;
* an expired, used, refused or unknown code gets ``expired_token`` (or
  ``access_denied`` once, for a refusal) ;
* the token is created when the board fetches it, never held before :
  the store keeps only the player who said yes.

Only the digest of a ``device_code`` is kept. The store lives in the
memory of the viewer's one worker, like the rate limits : a restart drops
the pairings under way, and the board asks again.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

#: Seconds a code lives (design #351 §4).
EXPIRES_IN = 600
#: Seconds between two polls of the board ; ``slow_down`` adds :data:`SLOW_DOWN_STEP`.
INTERVAL = 5
SLOW_DOWN_STEP = 5
#: No 0/O nor 1/I : a code read aloud or copied by hand stays one code.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
#: Pairings under way at once : past it, a new one is refused (memory stays bounded).
MAX_PENDING = 1000
DEVICE_NAME_MAX = 40
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_USER_CODE_RE = re.compile(r"^[A-Z0-9]{8}$")


class PairingError(Exception):
    """The RFC 8628 error of a poll, or a refusal of the player's step."""

    def __init__(self, error: str) -> None:
        super().__init__(error)
        self.error = error


def _digest(device_code: str) -> str:
    return hashlib.sha256(device_code.encode()).hexdigest()


def normalize_user_code(raw: object) -> str | None:
    """``ABCD-EFGH`` as typed (case, spaces, dash) ; ``None`` if it cannot be one."""
    if not isinstance(raw, str):
        return None
    code = re.sub(r"[\s-]", "", raw).upper()
    return code if _USER_CODE_RE.match(code) else None


def device_name_of(raw: object) -> str:
    """The name the board announces, without control characters ; « échiquier » if none."""
    name = _CONTROL_RE.sub("", raw).strip() if isinstance(raw, str) else ""
    return name[:DEVICE_NAME_MAX] or "échiquier"


@dataclass
class _Pairing:
    user_code: str
    device_name: str
    requested_at: str
    expires: float
    interval: int = INTERVAL
    last_poll: float | None = None
    approved_by: str | None = None
    denied: bool = False
    #: Set once the player answered : the code serves once.
    answered: bool = field(default=False)


class Pairings:
    """The pairings under way, in this process."""

    def __init__(self) -> None:
        self._by_device: dict[str, _Pairing] = {}
        self._by_user: dict[str, str] = {}

    def _drop_expired(self, now: float) -> None:
        for key in [k for k, p in self._by_device.items() if p.expires <= now]:
            self._forget(key)

    def _forget(self, key: str) -> None:
        pairing = self._by_device.pop(key, None)
        if pairing is not None:
            self._by_user.pop(pairing.user_code, None)

    def start(self, device_name: object, now: float | None = None) -> dict:
        """A new pairing : the RFC 8628 answer, ``device_code`` in clear this once."""
        now = time.monotonic() if now is None else now
        self._drop_expired(now)
        if len(self._by_device) >= MAX_PENDING:
            raise PairingError("too_many_pending")
        while True:
            user_code = "".join(secrets.choice(ALPHABET) for _ in range(8))
            if user_code not in self._by_user:
                break
        device_code = secrets.token_urlsafe(32)
        key = _digest(device_code)
        self._by_device[key] = _Pairing(
            user_code=user_code,
            device_name=device_name_of(device_name),
            requested_at=datetime.now(UTC).isoformat(timespec="seconds"),
            expires=now + EXPIRES_IN,
        )
        self._by_user[user_code] = key
        return {
            "device_code": device_code,
            "user_code": f"{user_code[:4]}-{user_code[4:]}",
            "verification_uri": "/appairer",
            "expires_in": EXPIRES_IN,
            "interval": INTERVAL,
        }

    def _waiting(self, raw_user_code: object, now: float) -> _Pairing:
        """The pairing of a code still waiting for its player ; else ``invalid_code``."""
        self._drop_expired(now)
        code = normalize_user_code(raw_user_code)
        key = self._by_user.get(code or "")
        pairing = self._by_device.get(key or "")
        if pairing is None or pairing.answered:
            raise PairingError("invalid_code")
        return pairing

    def describe(self, raw_user_code: object, now: float | None = None) -> dict:
        """What the player confirms : the board's announced name and the hour it asked."""
        pairing = self._waiting(raw_user_code, time.monotonic() if now is None else now)
        return {"device_name": pairing.device_name, "requested_at": pairing.requested_at}

    def answer(
        self, raw_user_code: object, player_id: str, *, accept: bool, now: float | None = None
    ) -> dict:
        """The player's yes or no ; the code serves no more after."""
        pairing = self._waiting(raw_user_code, time.monotonic() if now is None else now)
        pairing.answered = True
        if accept:
            pairing.approved_by = player_id
        else:
            pairing.denied = True
        return {"device_name": pairing.device_name, "paired": accept}

    def poll(self, device_code: object, now: float | None = None) -> tuple[str, str]:
        """``(player_id, device_name)`` once the player said yes ; :class:`PairingError` else.

        The pairing is forgotten when it answers the player's yes : the
        caller creates the token at once, and a second poll gets
        ``expired_token``.
        """
        now = time.monotonic() if now is None else now
        self._drop_expired(now)
        key = _digest(device_code) if isinstance(device_code, str) else ""
        pairing = self._by_device.get(key)
        if pairing is None:
            raise PairingError("expired_token")
        if pairing.last_poll is not None and now - pairing.last_poll < pairing.interval:
            pairing.interval += SLOW_DOWN_STEP
            pairing.last_poll = now
            raise PairingError("slow_down")
        pairing.last_poll = now
        if pairing.denied:
            self._forget(key)
            raise PairingError("access_denied")
        if pairing.approved_by is None:
            raise PairingError("authorization_pending")
        self._forget(key)
        return pairing.approved_by, pairing.device_name
