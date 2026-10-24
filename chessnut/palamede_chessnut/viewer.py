"""The viewer's HTTP API, as the bridge uses it : standard library only."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirected POST is reported, not followed (review Admin #71).

    Following it would send the move nowhere useful while the bridge
    counts it as delivered ; the 3xx comes back as an ``HTTPError``.
    """

    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def post(url: str, token: str, payload: dict[str, Any] | None = None) -> tuple[int | None, str]:
    """POST with the player token : ``(status, body excerpt)``, status ``None`` if unreachable.

    The token travels in an unredirected header : even a redirect that
    some other opener followed would not carry it (review Admin #71).
    """
    request = urllib.request.Request(
        url,
        data=json.dumps(payload or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    request.add_unredirected_header("Authorization", f"Bearer {token}")
    try:
        with _OPENER.open(request, timeout=10) as response:
            return int(response.status), ""
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:200]
    except OSError as exc:
        return None, str(exc)


def get_json(url: str) -> Any:
    """GET a JSON document (no token) ; raises ``OSError`` / ``ValueError``."""
    with urllib.request.urlopen(url, timeout=10) as response:
        return json.load(response)
