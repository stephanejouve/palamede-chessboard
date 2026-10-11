"""Chess arbiter daemon — v0 skeleton.

Runs out-of-session (LaunchAgent-friendly) : polls the arbiter's Talk
inbox JSONL for new messages, extracts a SAN move candidate from each,
validates via :func:`palamede_chessboard.chess_arbiter.validate_san_strict`,
applies to the game PGN (via :class:`palamede_chessboard.chessboard_state.GameState`),
notifies the next player in 1:1 Talk with the FEN + last 6 half-moves.

Rationale : partie 004 consumed ~40 Junior-session turns just to arbitrate
moves. A stand-alone daemon eliminates the session dependency ; players
can then poll ``/api/positions`` ETag on the viewer for a machine-friendly
"my turn" signal (side chantier).

v0 scope :

- One game at a time, driven by a JSON config on disk.
- Talk-only I/O (arbiter reads player 1:1 rooms, writes back 1:1).
- No commentary/analysis (the fair-play server-side gating is a separate
  PR ; the daemon just enforces move legality + protocol).
- Idempotent : the inbox cursor is persisted so a restart replays nothing.

Usage :

.. code-block:: bash

    poetry run chess-arbiter-daemon \\
        --config /Users/Shared/games/arbiter-004.config.json

Config example (``arbiter-004.config.json``) :

.. code-block:: json

    {
        "game_id": "admin-vs-leader-004",
        "arbiter_inbox": "/Users/magma/.local/share/magma-cycling/talk_inbox/devjunior.jsonl",
        "cursor_path": "/Users/Shared/games/arbiter-004.cursor",
        "log_path": "/Users/Shared/games/arbiter-log-004.md",
        "players": {
            "white": {"actor_id": "admin", "room": "buzevc2i"},
            "black": {"actor_id": "devleader", "room": "pbbbmnr2"}
        },
        "team_room": "b276xzrb",
        "poll_interval_seconds": 1.0
    }
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import chess

from palamede_chessboard.chess_arbiter import move_request_context, validate_san_strict
from palamede_chessboard.chessboard_state import GAMES_DIR, GameState, IllegalMoveError

log = logging.getLogger("chess_arbiter_daemon")


def arbiter_pidfile_name(game_id: str) -> str:
    """Return the per-game pidfile basename (CD review PR #22 : the install
    is per-partie, a shared ``.arbiter-daemon.pid`` would race the 2nd
    daemon into a ``KeepAlive`` crash-loop)."""
    return f".arbiter-daemon-{game_id}.pid"


# ---- SAN extraction -----------------------------------------------------

# Accept a SAN token wrapped in bold markdown (``**SAN**``) — the pattern
# every player has used across parties 1-4. Falls back to a looser regex
# if needed, but v0 stays strict to avoid false positives on commentary.
_BOLD_SAN_RE = re.compile(
    r"\*\*("
    r"O-O-O|O-O|"  # castling
    r"[NBRQK]?[a-h]?[1-8]?x?[a-h][1-8](?:=[NBRQ])?[+#]?"  # pieces + pawns
    r")\*\*"
)


def extract_san_candidate(message: str) -> str | None:
    """Return the first ``**SAN**`` token found in ``message``, or ``None``.

    Multiple bold tokens => first wins (the player's declared move) ; any
    trailing commentary tokens are treated as prose and ignored.
    """
    m = _BOLD_SAN_RE.search(message)
    return m.group(1) if m else None


# ---- config -------------------------------------------------------------


@dataclass(frozen=True)
class PlayerConfig:
    actor_id: str
    room: str


@dataclass(frozen=True)
class DaemonConfig:
    game_id: str
    arbiter_inbox: Path
    cursor_path: Path
    log_path: Path
    white: PlayerConfig
    black: PlayerConfig
    team_room: str | None
    poll_interval_seconds: float
    games_dir: Path

    @classmethod
    def from_json(cls, path: Path) -> DaemonConfig:
        data = json.loads(path.read_text(encoding="utf-8"))
        players = data["players"]
        raw_games_dir = data.get("games_dir") or os.environ.get("MCP_CHESSBOARD_GAMES_DIR")
        games_dir = Path(raw_games_dir) if raw_games_dir else GAMES_DIR
        return cls(
            game_id=data["game_id"],
            arbiter_inbox=Path(data["arbiter_inbox"]),
            cursor_path=Path(data["cursor_path"]),
            log_path=Path(data["log_path"]),
            white=PlayerConfig(**players["white"]),
            black=PlayerConfig(**players["black"]),
            team_room=data.get("team_room"),
            poll_interval_seconds=float(data.get("poll_interval_seconds", 1.0)),
            games_dir=games_dir,
        )


# ---- cursor persistence -------------------------------------------------


def _read_cursor(path: Path) -> int:
    if not path.exists():
        return 0
    txt = path.read_text(encoding="utf-8").strip()
    return int(txt) if txt else 0


def _write_cursor(path: Path, line_no: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(str(line_no), encoding="utf-8")
    tmp.replace(path)


# ---- Talk send indirection (test seam) ----------------------------------


def _default_send_message(message: str, room_token: str) -> None:
    from outillages.nextcloud_talk import send_message  # type: ignore[import-not-found]

    send_message(message, room_token=room_token)


# ---- daemon core --------------------------------------------------------


@dataclass
class ArbiterDaemon:
    """One instance per game. Not thread-safe."""

    config: DaemonConfig
    send: Callable[[str, str], None] = _default_send_message

    def _load_state(self) -> GameState:
        return GameState.load(self.config.game_id, games_dir=self.config.games_dir)

    def _player_config_for(self, actor_id: str) -> PlayerConfig | None:
        if actor_id == self.config.white.actor_id:
            return self.config.white
        if actor_id == self.config.black.actor_id:
            return self.config.black
        return None

    def _next_player(self, board: chess.Board) -> PlayerConfig:
        return self.config.white if board.turn == chess.WHITE else self.config.black

    def _expected_player(self, board: chess.Board) -> PlayerConfig:
        return self._next_player(board)

    def _append_log(self, entry: str) -> None:
        self.config.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config.log_path.open("a", encoding="utf-8") as f:
            f.write(entry)

    def process_message(self, entry: dict) -> str:
        """Process one inbox line. Returns an outcome tag for tests.

        Outcomes :

        - ``"skip:not-a-player"``     — sender isn't white or black
        - ``"skip:no-san"``           — no bold SAN token in the message
        - ``"skip:wrong-turn"``       — sender played but it wasn't their turn
        - ``"reject:<reason>"``       — SAN failed strict validation
        - ``"apply"``                 — move applied, next player notified
        - ``"game-over"``             — move ended the game
        """
        actor_id = entry.get("actor_id", "")
        message = entry.get("message", "")

        player = self._player_config_for(actor_id)
        if player is None:
            return "skip:not-a-player"

        san = extract_san_candidate(message)
        if san is None:
            return "skip:no-san"

        state = self._load_state()

        if self._expected_player(state.board).actor_id != actor_id:
            self.send(
                (
                    f"♟️ Arbitre : coup `{san}` reçu mais ce n'est pas ton "
                    f"tour (trait aux {'blancs' if state.board.turn == chess.WHITE else 'noirs'}). "
                    "Attends la notification arbitrage."
                ),
                player.room,
            )
            return "skip:wrong-turn"

        strict = validate_san_strict(state.board, san)
        if not strict.ok:
            ctx = move_request_context(state.board)
            self.send(
                (
                    f"♟️ Arbitre : coup `{san}` rejeté — **{strict.reason}**.\n\n"
                    f"FEN : `{ctx['fen']}`\n\n"
                    f"6 derniers demi-coups : `{' '.join(ctx['last_moves']) or '(aucun)'}`\n\n"
                    "Rejeu libre."
                ),
                player.room,
            )
            self._append_log(
                f"\n### Rejet — {san} par {actor_id}\n"
                f"- Reason: {strict.reason}\n"
                f"- FEN: {ctx['fen']}\n"
            )
            return f"reject:{strict.reason}"

        try:
            outcome = state.play_move(san, by=actor_id)
        except IllegalMoveError as exc:
            # Belt-and-braces : should not happen after validate_san_strict.
            return f"reject:{exc}"

        if outcome["is_game_over"]:
            self._append_log(
                f"\n### Fin de partie — {outcome['result']} après {san} ({actor_id})\n"
            )
            if self.config.team_room:
                self.send(
                    (
                        f"🏁 **Chess {self.config.game_id} — {outcome['result']}** "
                        f"(final: {san} par {actor_id})."
                    ),
                    self.config.team_room,
                )
            return "game-over"

        next_player = self._next_player(state.board)
        ctx = move_request_context(state.board)
        self.send(
            (
                f"♟️ Chess {self.config.game_id} — À toi (coup "
                f"{ctx['ply'] // 2 + 1}).\n\n"
                f"FEN : `{ctx['fen']}`\n\n"
                f"6 derniers demi-coups : `{' '.join(ctx['last_moves'])}`\n"
            ),
            next_player.room,
        )
        return "apply"

    def poll_once(self) -> int:
        """Read new inbox lines, process each. Returns count of new lines."""
        if not self.config.arbiter_inbox.exists():
            return 0
        with self.config.arbiter_inbox.open("r", encoding="utf-8") as f:
            all_lines = f.readlines()
        cursor = _read_cursor(self.config.cursor_path)
        new_lines = all_lines[cursor:]
        for offset, raw in enumerate(new_lines, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                log.warning("Bad JSON line at %d", cursor + offset)
                continue
            try:
                self.process_message(entry)
            except Exception:
                log.exception(
                    "Handler crashed on line %d (msg_id=%s), advancing cursor",
                    cursor + offset,
                    entry.get("id"),
                )
        _write_cursor(self.config.cursor_path, cursor + len(new_lines))
        return len(new_lines)

    def run_forever(self) -> None:
        log.info("chess_arbiter_daemon started for game=%s", self.config.game_id)
        with pidfile_lifecycle(self.config.games_dir, self.config.game_id):
            while True:
                try:
                    self.poll_once()
                except Exception:
                    log.exception("poll_once crashed, sleeping before retry")
                time.sleep(self.config.poll_interval_seconds)


# ---- pidfile lifecycle --------------------------------------------------


@contextlib.contextmanager
def pidfile_lifecycle(games_dir: Path, game_id: str):
    """Write ``<games_dir>/.arbiter-daemon-<game_id>.pid`` on entry, remove on exit.

    Also installs SIGTERM/SIGINT handlers so the cleanup runs even when the
    daemon is killed by launchd or ``kill <pid>``. Idempotent per game :
    if another live PID already holds the per-game marker, we raise
    ``RuntimeError`` rather than overwrite (avoid two arbiters for the
    same partie). Concurrent daemons on different ``game_id`` cohabit
    peacefully (CD review PR #22 : fix the KeepAlive crash-loop on 2e
    partie that shared ``.arbiter-daemon.pid``).
    """
    games_dir.mkdir(parents=True, exist_ok=True)
    marker = games_dir / arbiter_pidfile_name(game_id)
    if marker.exists():
        try:
            existing = int(marker.read_text(encoding="utf-8").strip())
            os.kill(existing, 0)
        except (ValueError, OSError):
            log.warning("stale pidfile at %s, overwriting", marker)
        else:
            raise RuntimeError(
                f"Another arbiter daemon (pid={existing}) already holds "
                f"{marker}. Refuse to double-claim."
            )
    # Atomic write : temp file + rename (avoid a concurrent reader seeing an
    # empty or partial pidfile between open and write). ``Path.replace``
    # maps to POSIX ``rename(2)`` which is atomic on same filesystem.
    tmp = marker.with_suffix(marker.suffix + ".tmp")
    tmp.write_text(str(os.getpid()), encoding="utf-8")
    tmp.replace(marker)
    log.info("wrote arbiter pidfile %s (pid=%d)", marker, os.getpid())

    def _cleanup(*_args):
        with contextlib.suppress(FileNotFoundError):
            marker.unlink()
        log.info("removed arbiter pidfile %s", marker)

    previous_term = signal.signal(signal.SIGTERM, lambda *a: (_cleanup(), sys.exit(0)))
    previous_int = signal.signal(signal.SIGINT, lambda *a: (_cleanup(), sys.exit(0)))
    try:
        yield marker
    finally:
        _cleanup()
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


# ---- CLI ----------------------------------------------------------------


DEFAULT_GAMES_DIR_FALLBACK = Path("/Users/Shared/games")


def _resolve_games_dir(cli_value: str | None) -> Path:
    """Resolution order per issue #257 : CLI ``--games-dir``, then env
    ``MCP_CHESSBOARD_GAMES_DIR``, then fallback ``/Users/Shared/games``.
    """
    if cli_value:
        return Path(cli_value)
    env = os.environ.get("MCP_CHESSBOARD_GAMES_DIR", "").strip()
    if env:
        return Path(env)
    return DEFAULT_GAMES_DIR_FALLBACK


def _ensure_talk_sender_importable() -> bool:
    """Return True iff ``outillages.nextcloud_talk`` imports cleanly.

    Called at startup before entering the arbiter mode loop : the daemon
    cannot notify the next player if the Talk sender isn't installed, and
    ``poll_once`` swallows the ImportError into the generic ``Exception``
    handler so the failure would otherwise stay silent (CD review PR #22).
    """
    try:
        import outillages.nextcloud_talk  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:
        log.error(
            "fatal: outillages.nextcloud_talk unavailable (%s). Required for "
            "arbiter mode : install ``outillages`` or run guardian mode "
            "(``--game-id`` without ``--config``).",
            exc,
        )
        return False
    return True


def _run_guardian(games_dir: Path, game_id: str, poll_interval: float) -> None:
    """Hold the per-game pidfile and idle. Satisfies ``chess_create_game``
    contract without the full Talk-polling arbiter ; SIGTERM/SIGINT exit
    cleanly via ``pidfile_lifecycle``.
    """
    log.info("guardian mode : games_dir=%s game_id=%s pid=%d", games_dir, game_id, os.getpid())
    with pidfile_lifecycle(games_dir, game_id):
        while True:
            time.sleep(poll_interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Chess arbiter daemon. Two modes : (guardian) publish the "
            "pidfile expected by ``chess_create_game`` and idle ; (arbiter) "
            "poll a Talk inbox and validate moves against a specific game "
            "(requires ``--config``)."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to JSON config file (activates full arbiter mode).",
    )
    parser.add_argument(
        "--games-dir",
        type=str,
        default=None,
        help=(
            "Games directory whose pidfile the daemon claims. Falls back to "
            "``$MCP_CHESSBOARD_GAMES_DIR`` env, then ``/Users/Shared/games``. "
            "Ignored when ``--config`` is provided (config wins)."
        ),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=1.0,
        help="Guardian idle-loop sleep interval, in seconds.",
    )
    parser.add_argument(
        "--game-id",
        type=str,
        default=None,
        help=(
            "Game identifier used to name the per-game pidfile in "
            "``games_dir``. Required in guardian mode. Ignored when "
            "``--config`` is provided (config.game_id wins)."
        ),
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level (DEBUG/INFO/WARNING).")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Poll a single time and exit (arbiter mode only, smoke test).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.config is not None:
        if not _ensure_talk_sender_importable():
            return 2
        config = DaemonConfig.from_json(args.config)
        daemon = ArbiterDaemon(config=config)
        if args.once:
            daemon.poll_once()
            return 0
        daemon.run_forever()
        return 0

    # Guardian mode : no config, just hold the per-game pidfile.
    if not args.game_id:
        parser.error("--game-id is required in guardian mode (no --config).")
    games_dir = _resolve_games_dir(args.games_dir)
    _run_guardian(games_dir, args.game_id, args.poll_interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
