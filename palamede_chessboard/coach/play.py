"""Exercises played as games (#95) : start, judge, answer, never the solution.

An exercise is played in an unrated game ``exo.<player>.<stamp>-<hex>``
from its position, the player against the ``coach``. Only the viewer
creates and plays them, under a player's token : the prefix is reserved
at every game creation, and the MCP refuses to change these games while
it does not authenticate (Admin, #95). The older ``exo-…`` games (with a
dash) are ordinary games and stay so.

* ``find_move`` — the right move is played and ends the game, won. Any
  other is refused (409) and not played ; the exercise is ``failed``
  from the first wrong try, and the player may try again in the same
  game. A mate slower than the solution says so (Leader, #95).
* ``attacked`` — answered with the squares (:func:`answer`), not a move.

No engine here : the solution was computed when the exercise was drawn.
It is never in a game, and an exercise shows it only once ``solved``
(:func:`public_view`) : a live game that reaches the position would
otherwise read it around the engine guard (Admin, #95).

Locks : a game's lock, then the player's store, never the other way.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from pathlib import Path

import chess

from palamede_chessboard.chessboard_state import (
    GameAlreadyExistsError,
    GameMetadata,
    GameState,
    IllegalMoveError,
    TimeControl,
)
from palamede_chessboard.clock_moves import game_lock
from palamede_chessboard.coach.store import read_exercises, update_exercise

EXO_PREFIX = "exo."
EVENT = "Exercice"
#: The other side of an exercise game, never rated : a name no account can
#: carry (a space), so no token ever acts for it in someone's exercise.
OPPONENT = "Coach Palamède"
SOLVED_NOTE = "exercice réussi"
ABANDONED_NOTE = "exercice abandonné"
NOT_THE_MOVE = "Ce n'est pas le coup"
FASTER_MATE = "Il y a plus rapide : un mat en moins de coups"
#: Tools that change a game : refused on ``exo.*`` by the MCP (Admin, #95).
MCP_REFUSED_TOOLS = frozenset(
    {
        "chess_play_move",
        "chess_resign",
        "chess_offer_draw",
        "chess_agree_draw",
        "chess_decline_draw",
    }
)
#: What only a solved exercise shows.
SOLUTION_FIELDS = ("solution_uci", "solution_san", "solution_squares", "slower_mates")
_ID_ATTEMPTS = 5


class ExerciseError(Exception):
    """A refusal the viewer turns into ``status`` with ``message``."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def is_exercise_game(game_id: object) -> bool:
    """``exo.*``, whatever its case ; never the older ``exo-…``."""
    return str(game_id or "").lower().startswith(EXO_PREFIX)


def public_view(exercise: dict) -> dict:
    """The exercise as its player may read it : no solution before it is solved."""
    if exercise.get("state") == "solved":
        return dict(exercise)
    return {k: v for k, v in exercise.items() if k not in SOLUTION_FIELDS}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _player_side(state: GameState, player_id: str) -> str:
    return "white" if state.metadata.white == player_id else "black"


def _finish(state: GameState, winner: str, note: str) -> None:
    state.metadata.result = "1-0" if winner == "white" else "0-1"
    state.metadata.termination = "normal"
    state.metadata.termination_note = note
    state.metadata.draw_offer = None
    state.save()


def _exercise_of_game(games_dir: Path, player_id: str, game_id: str) -> dict:
    for exercise in read_exercises(games_dir, player_id):
        if game_id in exercise.get("game_ids", []):
            return exercise
    raise ExerciseError(404, "Aucun exercice de ce joueur pour cette partie")


def _attempt(exercise_id: str, games_dir: Path, player_id: str, entry: dict, ok: bool) -> dict:
    """Record an attempt ; ``solved`` only if none failed in that game."""

    def change(exercise: dict) -> None:
        exercise.setdefault("attempts", []).append({**entry, "ok": ok, "at": _now()})
        in_game = [a for a in exercise["attempts"] if a.get("game_id") == entry["game_id"]]
        exercise["state"] = "solved" if all(a["ok"] for a in in_game) else "failed"

    return update_exercise(games_dir, player_id, exercise_id, change)


def _close_open_games(games_dir: Path, player_id: str) -> None:
    """One exercise at a time : an open one is abandoned, never left open."""
    for pgn in sorted(games_dir.glob(f"{EXO_PREFIX}{player_id}.*.pgn")):
        with game_lock(pgn.stem, games_dir):
            state = GameState.load(pgn.stem, games_dir=games_dir)
            if state.metadata.result == "*":
                winner = "black" if _player_side(state, player_id) == "white" else "white"
                _finish(state, winner, ABANDONED_NOTE)


def start(games_dir: Path, player_id: str, exercise_id: str) -> dict:
    """Open a game on the exercise's position ; ``{game_id, exercise}``."""
    exercise = next(
        (e for e in read_exercises(games_dir, player_id) if e.get("id") == exercise_id), None
    )
    if exercise is None:
        raise ExerciseError(404, "Exercice inconnu")
    _close_open_games(games_dir, player_id)
    side = exercise["side"]
    white, black = (player_id, OPPONENT) if side == "white" else (OPPONENT, player_id)
    for _ in range(_ID_ATTEMPTS):
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        metadata = GameMetadata(
            game_id=f"{EXO_PREFIX}{player_id}.{stamp}-{secrets.token_hex(2)}",
            event=EVENT,
            white=white,
            black=black,
            time_control=TimeControl(format="unrated"),
            rated=False,
        )
        try:
            state = GameState.create(metadata, games_dir=games_dir, starting_fen=exercise["fen"])
            break
        except GameAlreadyExistsError:
            continue
    else:
        raise ExerciseError(503, "Aucun identifiant de partie libre, réessayez")
    game_id = state.metadata.game_id
    exercise = update_exercise(
        games_dir, player_id, exercise_id, lambda e: e.setdefault("game_ids", []).append(game_id)
    )
    return {"game_id": game_id, "exercise": public_view(exercise)}


def play_move(state: GameState, player_id: str, move_san: str, games_dir: Path) -> dict:
    """Judge the move of ``player_id`` in an exercise game, under its lock.

    The right move is played and wins the game ; any other is refused,
    :class:`ExerciseError` 409, and the board is left as it was.
    """
    game_id = state.metadata.game_id
    if state.metadata.result != "*":
        raise ExerciseError(409, "Cet exercice est terminé")
    exercise = _exercise_of_game(games_dir, player_id, game_id)
    if exercise["kind"] != "find_move":
        raise ExerciseError(409, "Répondez en désignant les cases des pièces en prise")
    try:
        uci = state.board.parse_san(move_san).uci()
    except ValueError as exc:
        raise IllegalMoveError(f"Move {move_san!r} rejected: {exc}") from exc
    entry = {"game_id": game_id, "move": uci}
    if uci != exercise["solution_uci"]:
        _attempt(exercise["id"], games_dir, player_id, entry, ok=False)
        faster = uci in exercise.get("slower_mates", [])
        raise ExerciseError(409, FASTER_MATE if faster else NOT_THE_MOVE)
    side = _player_side(state, player_id)
    result = state.play_move(move_san, by=player_id)
    _finish(state, side, SOLVED_NOTE)
    _attempt(exercise["id"], games_dir, player_id, entry, ok=True)
    return {**result, "result": state.metadata.result}


def answer(games_dir: Path, player_id: str, exercise_id: str, squares: list[str]) -> dict:
    """Answer « what is attacked ? » in the exercise's open game ; the exercise.

    The right set of squares wins the game ; any other is refused (409)
    and the player may answer again.
    """
    exercise = next(
        (e for e in read_exercises(games_dir, player_id) if e.get("id") == exercise_id), None
    )
    if exercise is None:
        raise ExerciseError(404, "Exercice inconnu")
    if exercise["kind"] != "attacked":
        raise ExerciseError(409, "Cet exercice se résout par un coup")
    if not exercise.get("game_ids"):
        raise ExerciseError(409, "Lancez d'abord l'exercice")
    game_id = exercise["game_ids"][-1]
    given = sorted({str(s).strip().lower() for s in squares})
    if any(s not in chess.SQUARE_NAMES for s in given):
        raise ExerciseError(422, "Une case s'écrit de a1 à h8")
    with game_lock(game_id, games_dir):
        state = GameState.load(game_id, games_dir=games_dir)
        if state.metadata.result != "*":
            raise ExerciseError(409, "Cet exercice est terminé")
        entry = {"game_id": game_id, "squares": given}
        if given != exercise["solution_squares"]:
            _attempt(exercise_id, games_dir, player_id, entry, ok=False)
            raise ExerciseError(409, "Ce ne sont pas les bonnes cases")
        _finish(state, _player_side(state, player_id), SOLVED_NOTE)
        exercise = _attempt(exercise_id, games_dir, player_id, entry, ok=True)
    return public_view(exercise)
