"""Tests for :mod:`palamede_chessboard.chess_arbiter_daemon`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from palamede_chessboard.chess_arbiter_daemon import (
    ArbiterDaemon,
    DaemonConfig,
    PlayerConfig,
    extract_san_candidate,
)
from palamede_chessboard.chessboard_state import GameMetadata, GameState

# ---- SAN extraction -----------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Coup 1 Blancs : **e4** — King's Pawn.", "e4"),
        ("Reprise **exd5** ouvre colonne e.", "exd5"),
        ("Longue diagonale, **Rxb1+** échec.", "Rxb1+"),
        ("Fool's mate **Qh4#** !", "Qh4#"),
        ("Promotion **e8=Q+**.", "e8=Q+"),
        ("Grand roque **O-O-O**.", "O-O-O"),
        ("Petit roque **O-O** simple.", "O-O"),
        ("Prose sans SAN bold.", None),
        ("**not-a-san** bold token.", None),
        ("Multiple **Nf3** then **e4** wins first.", "Nf3"),
    ],
)
def test_extract_san_candidate(message: str, expected: str | None) -> None:
    assert extract_san_candidate(message) == expected


# ---- daemon fixtures ----------------------------------------------------


@pytest.fixture
def games_dir(tmp_path: Path) -> Path:
    d = tmp_path / "games"
    d.mkdir()
    return d


@pytest.fixture
def config(tmp_path: Path, games_dir: Path) -> DaemonConfig:
    inbox = tmp_path / "inbox.jsonl"
    inbox.touch()
    return DaemonConfig(
        game_id="admin-vs-leader-test",
        arbiter_inbox=inbox,
        cursor_path=tmp_path / "cursor",
        log_path=tmp_path / "arbiter-log.md",
        white=PlayerConfig(actor_id="admin", room="ROOM_ADMIN"),
        black=PlayerConfig(actor_id="devleader", room="ROOM_LEADER"),
        team_room="ROOM_TEAM",
        poll_interval_seconds=0.01,
        games_dir=games_dir,
    )


@pytest.fixture
def game(games_dir: Path) -> GameState:
    return GameState.create(
        GameMetadata(
            game_id="admin-vs-leader-test",
            event="test",
            white="admin",
            black="devleader",
            arbiter="devjunior",
        ),
        games_dir=games_dir,
    )


@pytest.fixture
def daemon(config: DaemonConfig, game: GameState):
    """Daemon with in-memory ``send`` (captures messages).

    ``DaemonConfig.games_dir`` propagates to ``GameState.load`` via
    ``_load_state`` — no monkeypatching needed since the daemon now
    takes ``games_dir`` in its config.
    """
    captured: list[tuple[str, str]] = []

    def fake_send(message: str, room: str) -> None:
        captured.append((message, room))

    d = ArbiterDaemon(config=config, send=fake_send)
    d.captured = captured  # type: ignore[attr-defined]
    return d


# ---- process_message ----------------------------------------------------


def _msg(actor_id: str, message: str, msg_id: int = 1) -> dict:
    return {"id": msg_id, "actor_id": actor_id, "message": message}


def test_skip_non_player(daemon: ArbiterDaemon) -> None:
    outcome = daemon.process_message(_msg("stranger", "Coup **e4**"))
    assert outcome == "skip:not-a-player"
    assert daemon.captured == []


def test_skip_no_san(daemon: ArbiterDaemon) -> None:
    outcome = daemon.process_message(_msg("admin", "juste du bruit"))
    assert outcome == "skip:no-san"
    assert daemon.captured == []


def test_apply_legal_first_move_notifies_black(daemon: ArbiterDaemon) -> None:
    outcome = daemon.process_message(_msg("admin", "Coup 1 : **e4**"))
    assert outcome == "apply"
    assert len(daemon.captured) == 1
    message, room = daemon.captured[0]
    assert room == "ROOM_LEADER"
    assert "FEN" in message
    assert "e4" in message


def test_reject_ghost_capture(daemon: ArbiterDaemon) -> None:
    # Reach a position where b6 is empty for admin's ghost Bxb6.
    daemon.process_message(_msg("admin", "**e4**"))
    daemon.process_message(_msg("devleader", "**c5**"))
    daemon.process_message(_msg("admin", "**Nf3**"))
    daemon.process_message(_msg("devleader", "**d6**"))
    daemon.process_message(_msg("admin", "**d4**"))
    daemon.process_message(_msg("devleader", "**cxd4**"))  # end at white-to-move
    daemon.captured.clear()
    outcome = daemon.process_message(_msg("admin", "**Bxb6**"))
    assert outcome.startswith("reject:")
    assert len(daemon.captured) == 1
    message, room = daemon.captured[0]
    assert room == "ROOM_ADMIN"
    assert "Bxb6" in message
    assert "rejeté" in message
    assert "Rejeu libre" in message


def test_wrong_turn_message(daemon: ArbiterDaemon) -> None:
    # Black plays first — must be rejected as wrong turn.
    outcome = daemon.process_message(_msg("devleader", "**e5**"))
    assert outcome == "skip:wrong-turn"
    assert len(daemon.captured) == 1
    message, room = daemon.captured[0]
    assert room == "ROOM_LEADER"
    assert "n'est pas ton tour" in message


def test_game_over_notification(daemon: ArbiterDaemon) -> None:
    # Fool's mate.
    daemon.process_message(_msg("admin", "**f3**"))
    daemon.process_message(_msg("devleader", "**e5**"))
    daemon.process_message(_msg("admin", "**g4**"))
    daemon.captured.clear()
    outcome = daemon.process_message(_msg("devleader", "**Qh4#**"))
    assert outcome == "game-over"
    # Team room + optionally player room ; check team room got it.
    team_msgs = [m for m, r in daemon.captured if r == "ROOM_TEAM"]
    assert len(team_msgs) == 1
    assert "0-1" in team_msgs[0]


# ---- poll_once + cursor -------------------------------------------------


def test_poll_advances_cursor_and_persists(daemon: ArbiterDaemon, config: DaemonConfig) -> None:
    config.arbiter_inbox.write_text(
        json.dumps(_msg("admin", "**e4**")) + "\n" + json.dumps(_msg("devleader", "**c5**")) + "\n",
        encoding="utf-8",
    )
    processed = daemon.poll_once()
    assert processed == 2
    assert config.cursor_path.read_text(encoding="utf-8").strip() == "2"

    # Second poll : nothing new.
    processed = daemon.poll_once()
    assert processed == 0


def test_poll_resumes_from_cursor_on_restart(daemon: ArbiterDaemon, config: DaemonConfig) -> None:
    config.cursor_path.write_text("1", encoding="utf-8")
    config.arbiter_inbox.write_text(
        json.dumps(_msg("admin", "**e4**")) + "\n" + json.dumps(_msg("devleader", "**c5**")) + "\n",
        encoding="utf-8",
    )
    daemon.poll_once()
    # Only 1 SAN-bearing msg was picked up (the black one) and applied.
    # White never played -> Black's move must be wrong-turn.
    outcomes = [m for m, _ in daemon.captured]
    assert any("n'est pas ton tour" in m for m in outcomes)


# ---- pidfile lifecycle (contract with mcp_chessboard_server) ------------


def test_pidfile_lifecycle_writes_and_cleans(games_dir: Path) -> None:
    """The context manager writes our PID on entry and unlinks on exit."""
    import os as _os

    from palamede_chessboard.chess_arbiter_daemon import ARBITER_DAEMON_PIDFILE, pidfile_lifecycle

    marker = games_dir / ARBITER_DAEMON_PIDFILE
    assert not marker.exists()

    with pidfile_lifecycle(games_dir):
        assert marker.exists()
        assert marker.read_text(encoding="utf-8").strip() == str(_os.getpid())

    assert not marker.exists()


def test_pidfile_lifecycle_refuses_live_holder(games_dir: Path) -> None:
    """A second daemon on the same games_dir must refuse rather than
    silently overwrite a live PID's marker."""
    import os as _os

    from palamede_chessboard.chess_arbiter_daemon import ARBITER_DAEMON_PIDFILE, pidfile_lifecycle

    (games_dir / ARBITER_DAEMON_PIDFILE).write_text(str(_os.getpid()), encoding="utf-8")
    with pytest.raises(RuntimeError, match="already holds"):
        with pidfile_lifecycle(games_dir):
            pass


def test_pidfile_lifecycle_overwrites_stale(games_dir: Path) -> None:
    """A stale pidfile (dead PID) should be overwritten silently — the
    previous daemon crashed and we're recovering."""
    import os as _os

    from palamede_chessboard.chess_arbiter_daemon import ARBITER_DAEMON_PIDFILE, pidfile_lifecycle

    (games_dir / ARBITER_DAEMON_PIDFILE).write_text("999999999", encoding="utf-8")
    with pidfile_lifecycle(games_dir):
        assert (games_dir / ARBITER_DAEMON_PIDFILE).read_text(encoding="utf-8").strip() == str(
            _os.getpid()
        )


def test_pidfile_lifecycle_creates_dir(tmp_path: Path) -> None:
    """The daemon can be pointed at a games_dir that doesn't exist yet."""
    from palamede_chessboard.chess_arbiter_daemon import pidfile_lifecycle

    fresh = tmp_path / "brand-new-games-dir"
    assert not fresh.exists()
    with pidfile_lifecycle(fresh):
        assert fresh.is_dir()


# ---- CLI --games-dir resolution (issue #257) ----------------------------


def test_resolve_games_dir_cli_wins(monkeypatch, tmp_path: Path) -> None:
    from palamede_chessboard.chess_arbiter_daemon import _resolve_games_dir

    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", "/env/should/lose")
    assert _resolve_games_dir(str(tmp_path)) == tmp_path


def test_resolve_games_dir_env_fallback(monkeypatch, tmp_path: Path) -> None:
    from palamede_chessboard.chess_arbiter_daemon import _resolve_games_dir

    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    assert _resolve_games_dir(None) == tmp_path


def test_resolve_games_dir_default_fallback(monkeypatch) -> None:
    from palamede_chessboard.chess_arbiter_daemon import (
        DEFAULT_GAMES_DIR_FALLBACK,
        _resolve_games_dir,
    )

    monkeypatch.delenv("MCP_CHESSBOARD_GAMES_DIR", raising=False)
    assert _resolve_games_dir(None) == DEFAULT_GAMES_DIR_FALLBACK


def test_pidfile_write_is_atomic(games_dir: Path) -> None:
    """The pidfile is created via a ``.tmp`` file then renamed, so a
    concurrent reader never sees an empty or partial pidfile."""
    from palamede_chessboard.chess_arbiter_daemon import ARBITER_DAEMON_PIDFILE, pidfile_lifecycle

    marker = games_dir / ARBITER_DAEMON_PIDFILE
    tmp = marker.with_suffix(marker.suffix + ".tmp")
    with pidfile_lifecycle(games_dir):
        assert marker.exists()
        assert not tmp.exists(), "tmp must not linger after atomic rename"
