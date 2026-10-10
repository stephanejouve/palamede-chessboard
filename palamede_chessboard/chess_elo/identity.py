"""Agent identity normalisation for the Elo rating system.

Stéphane arbitrage 2026-10-03 : player names are collapsed to a
canonical form so a same agent is never split across casing or legacy
variants (``Leader`` / ``LEADER`` / ``devleader`` → ``leader``).
"""

from __future__ import annotations

#: Canonical agent identifiers. Any PGN ``White`` / ``Black`` value
#: that normalises to one of these is kept as-is ; non-canonical names
#: still pass through the alias table or ``strip().lower()`` fallback.
CANONICAL_IDENTITIES: frozenset[str] = frozenset({"admin", "leader", "junior", "claude-desktop"})

#: Alias table : historical / lowercased name → canonical identifier.
#: Stéphane arbitrage 2026-10-03 : ``devleader`` is Leader, same for
#: all casings of admin/leader. Any entry missing from this table and
#: not matching :data:`CANONICAL_IDENTITIES` falls back to its
#: ``strip().lower()`` form (new agents / unknown names stay distinct).
IDENTITY_ALIASES: dict[str, str] = {
    "devleader": "leader",
}


def normalize_identity(raw: str) -> str:
    """Return the canonical identifier for a PGN ``White``/``Black`` value.

    Normalisation steps :

    1. ``strip().lower()`` — removes trailing whitespace and casing noise
       (``Leader`` / ``LEADER`` / ``leader`` collapse to one key).
    2. :data:`IDENTITY_ALIASES` lookup — explicit mapping for legacy
       variants (``devleader`` → ``leader``).
    3. Fallback = the ``strip().lower()`` form, so unknown agents stay
       distinct instead of being swept into a canonical bucket by
       accident. Returns the empty string on ``raw=""`` or ``None``-ish.
    """
    if not raw:
        return ""
    key = raw.strip().lower()
    return IDENTITY_ALIASES.get(key, key)
