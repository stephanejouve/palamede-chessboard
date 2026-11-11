"""Lessons (``cours.*``, #323) : one server operation, whatever the interface.

A lesson is a session between a coach and a pupil where a move can be
taken back (next step of #323). The id is given here only : the prefix
``cours.`` is reserved (:data:`palamede_chessboard.paths.LESSON_PREFIX`),
so ``chess_create_game`` refuses it.

The owner's rule (11/11, #328) : the kind of session never depends on the
interface. The MCP tool ``chess_create_lesson`` and ``POST /api/lessons``
(viewer, Tablier) both call :func:`create_lesson`, which holds every rule
and every refusal ; they only add their own check of who asks.

The id follows the two lessons of 10/11, still readable as they are :
``cours.<theme>.<white>-<black>.<YYYYMMDD>-<NN>``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from palamede_chessboard import _atomic
from palamede_chessboard.chessboard_state import (
    ChessboardError,
    GameAlreadyExistsError,
    GameMetadata,
    GameState,
    TimeControl,
)
from palamede_chessboard.paths import GAME_ID_PATTERN, LESSON_PREFIX
from palamede_chessboard.players import registry

#: ``Mode`` header of a lesson (#323).
MODE = "cours"
#: Theme of a lesson in its id : the opening, the motif (``italienne``).
THEME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
#: Lessons a day for the same theme and players : ``-01`` to ``-99``.
MAX_PER_DAY = 99
#: Lock taken around the search of a free number and the creation, so two
#: interfaces asking at once never get the same id (``<games dir>/.cours-create.lock``).
LOCK_NAME = ".cours-create"


class LessonError(ChessboardError):
    """A lesson refused, with the reason in French, the same for every interface."""


def _player(raw: object, side: str, known: dict) -> str:
    """Normalised id of an account of the registry ; :class:`LessonError` otherwise."""
    try:
        player = registry.player_id_of(raw)
    except registry.RegistryError:
        raise LessonError(f"Joueur des {side} invalide : {raw!r}") from None
    if player not in known:
        raise LessonError(f"Joueur des {side} inconnu : {player}")
    return player


def _known_players(games_dir: Path) -> dict:
    try:
        return registry.load(games_dir)["players"]
    except registry.RegistryError as exc:
        raise LessonError("Registre des joueurs illisible : cours refusé") from exc


def lesson_id(theme: str, white: str, black: str, day: str, number: int) -> str:
    """``cours.<theme>.<white>-<black>.<day>-<NN>``."""
    return f"{LESSON_PREFIX}{theme}.{white}-{black}.{day}-{number:02d}"


def create_lesson(
    games_dir: Path,
    *,
    theme: object,
    white: object,
    black: object,
    arbiter: str | None = None,
) -> GameState:
    """Open a lesson : unrated, no clock, ``[Mode "cours"]`` ; the next free number of the day."""
    if not isinstance(theme, str) or not THEME_PATTERN.match(theme):
        raise LessonError("Thème invalide : a-z, 0-9 et tirets, 24 caractères au plus")
    known = _known_players(games_dir)
    white_id = _player(white, "blancs", known)
    black_id = _player(black, "noirs", known)
    if white_id == black_id:
        raise LessonError("Un cours se joue à deux : les blancs et les noirs diffèrent")
    day = datetime.now(UTC).strftime("%Y%m%d")
    if not GAME_ID_PATTERN.match(lesson_id(theme, white_id, black_id, day, MAX_PER_DAY)):
        raise LessonError("Identifiant du cours trop long : raccourcir le thème")
    with _atomic.locked(games_dir / LOCK_NAME):
        for number in range(1, MAX_PER_DAY + 1):
            metadata = GameMetadata(
                game_id=lesson_id(theme, white_id, black_id, day, number),
                event=f"Cours : {theme}",
                white=white_id,
                black=black_id,
                arbiter=arbiter or None,
                time_control=TimeControl(format="unrated"),
                rated=False,
                mode=MODE,
            )
            try:
                return GameState.create(metadata, games_dir=games_dir)
            except GameAlreadyExistsError:
                continue
    raise LessonError(f"Déjà {MAX_PER_DAY} cours aujourd'hui sur ce thème entre ces joueurs")
