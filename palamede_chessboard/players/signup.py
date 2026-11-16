"""Open sign-up (#351, PR 3) : anyone may create a player account (owner, 14/11).

A new account is a ``human`` player with a ``browser`` token, and, unless
refused, a ``recovery`` token shown once : the only way back in for whoever
loses their browser (owner, 14/11, question B). Nothing more : no arbiter,
no agent, no game ; the rights stay those of #34, playing one's own moves.

A name already used is **unavailable**, with one generic answer whatever
the reason, so the refusal does not tell who played (design #351 §3) :

* an account of the registry ;
* a name in the games' headers (``White``, ``Black``, the relay teams, the
  arbiter, the spectators, the annotator), even without an account :
  otherwise a newcomer would take a past player's games and Elo. A past
  player gets their name back from the operator (owner, 14/11, question C) ;
* a name of the players' preferences, and the agents' canonical names.

Names are compared after :func:`~palamede_chessboard.chess_elo.normalize_identity`,
as the Elo and the authorisation compare them. Only the headers are read,
not the moves ; a game that cannot be read refuses the sign-up rather
than let a name through.
"""

from __future__ import annotations

import re
from pathlib import Path

import chess.pgn

from palamede_chessboard.chess_elo import normalize_identity
from palamede_chessboard.chess_elo.identity import CANONICAL_IDENTITIES, IDENTITY_ALIASES
from palamede_chessboard.player_prefs import load_prefs
from palamede_chessboard.players import registry

#: Headers that name a person : a name found there is taken.
NAME_HEADERS = ("White", "Black", "WhiteTeam", "BlackTeam", "Arbiter", "Spectators", "Annotator")
#: Headers whose value is a comma-separated list of names.
LIST_HEADERS = frozenset({"WhiteTeam", "BlackTeam", "Spectators"})
DISPLAY_NAME_MAX = 64
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class SignupError(Exception):
    """A sign-up refused because of what was asked ; the reason in French."""


class NameUnavailable(SignupError):
    """The name is taken : the same answer whatever by whom."""


def _header_names(pgn: Path) -> set[str]:
    """The names of ``pgn``'s headers, read as the games are (python-chess), moves skipped."""
    names: set[str] = set()
    with pgn.open(encoding="utf-8-sig", errors="replace") as handle:
        while (headers := chess.pgn.read_headers(handle)) is not None:
            for tag in NAME_HEADERS:
                value = headers.get(tag, "")
                values = value.split(",") if tag in LIST_HEADERS else [value]
                names.update(normalize_identity(v) for v in values)
    return names


def names_in_history(games_dir: Path) -> set[str]:
    """Every name the games of ``games_dir`` carry, normalised.

    An ``OSError`` on a game is raised : a game that cannot be read may
    carry the name asked, so the caller refuses (fail-closed).
    """
    names: set[str] = set()
    if games_dir.is_dir():
        for pgn in games_dir.glob("*.pgn"):
            names |= _header_names(pgn)
    names.discard("")
    return names


def reserved_names(games_dir: Path) -> set[str]:
    """The names a sign-up may not take, besides the registry's accounts."""
    return (
        names_in_history(games_dir)
        | {normalize_identity(name) for name in load_prefs(games_dir)}
        | set(CANONICAL_IDENTITIES)
        | set(IDENTITY_ALIASES)
        | set(IDENTITY_ALIASES.values())
    )


def _display_name(raw: str, player_id: str) -> str:
    name = _CONTROL_RE.sub("", raw).strip()
    if len(name) > DISPLAY_NAME_MAX:
        raise SignupError(f"Nom affiché trop long : {DISPLAY_NAME_MAX} caractères au plus")
    return name or player_id


def sign_up(
    games_dir: Path, raw_id: object, *, display_name: str = "", recovery: bool, label: str
) -> tuple[str, str, str | None]:
    """Create the account : ``(player_id, browser token, recovery token or None)``.

    :class:`SignupError` for a name refused, :class:`NameUnavailable` for a
    name taken ; :class:`~palamede_chessboard.players.registry.RegistryError`
    or ``OSError`` when the registry or a game cannot be read.
    """
    try:
        player_id = registry.player_id_of(raw_id)
    except registry.RegistryError:
        raise SignupError(
            "Nom invalide : lettres a-z, chiffres, _ et -, 32 caractères au plus"
        ) from None
    shown = _display_name(display_name, player_id)
    if player_id in reserved_names(games_dir):
        raise NameUnavailable("Nom indisponible")
    issued = [(label, "browser")]
    if recovery:
        issued.append(("récupération", "recovery"))
    try:
        created = registry.add_player_with_tokens(
            games_dir, player_id, kind="human", display_name=shown, issued=issued
        )
    except registry.PlayerExists:
        raise NameUnavailable("Nom indisponible") from None
    return player_id, created[0], created[1] if recovery else None
