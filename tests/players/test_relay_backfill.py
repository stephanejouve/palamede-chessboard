"""One-off backfill of relay teams on games created before #281 (#282, point 6)."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from palamede_chessboard import cli_players
from palamede_chessboard.chessboard_state import GameMetadata, GameState
from palamede_chessboard.errors import ChessboardError, GameNotFoundError
from palamede_chessboard.players import registry
from palamede_chessboard.players.observe import roles_of
from palamede_chessboard.players.relay_backfill import JOURNAL_FILENAME, backfill_teams

TEAM = "relais-leader-admin-junior"
MEMBERS = ["leader", "admin", "junior"]


@pytest.fixture
def gdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv(registry.REGISTRY_ENV, raising=False)
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    registry.add_player(tmp_path, "stephane", kind="human")
    for pid in MEMBERS:
        registry.add_player(tmp_path, pid, kind="agent")
    state = GameState.create(
        GameMetadata(game_id="relais", white="stephane", black=TEAM, arbiter="leader", rated=False),
        games_dir=tmp_path,
    )
    for san in ("e4", "e5", "Qh5", "Nc6", "Bc4", "Nf6", "Qxf7#"):
        state.play_move(san)
    state.save()
    return tmp_path


def _journal(gdir: Path) -> Path:
    return registry.registry_path(gdir).parent / JOURNAL_FILENAME


def _pgn(gdir: Path, game_id: str = "relais") -> str:
    return (gdir / f"{game_id}.pgn").read_text(encoding="utf-8")


def test_backfill_adds_the_team_and_keeps_the_game(gdir: Path) -> None:
    before = GameState.load("relais", games_dir=gdir)
    done = backfill_teams(gdir, "relais", [], ["Leader", "admin", "junior"])
    assert (done["black_team"], done["white_team"], done["dry_run"]) == (MEMBERS, [], False)
    after = GameState.load("relais", games_dir=gdir)
    assert after.metadata.black_team == MEMBERS
    assert after.san_history == before.san_history
    assert (after.metadata.result, after.metadata.rated) == ("1-0", False)
    assert (after.metadata.white, after.metadata.black) == ("stephane", TEAM)
    assert roles_of(after)["seats"]["black"] == {TEAM, *MEMBERS}


def test_every_backfill_is_journaled_0600(gdir: Path) -> None:
    backfill_teams(gdir, "relais", [], MEMBERS)
    path = _journal(gdir)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    (entry,) = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert entry["game_id"] == "relais" and entry["black_team"] == MEMBERS
    assert entry["os_user"] and entry["ts"]


def test_dry_run_writes_nothing(gdir: Path) -> None:
    before = _pgn(gdir)
    done = backfill_teams(gdir, "relais", [], MEMBERS, dry_run=True)
    assert done["dry_run"] is True and done["black_team"] == MEMBERS
    assert _pgn(gdir) == before
    assert not _journal(gdir).exists()


def test_a_second_backfill_is_refused(gdir: Path) -> None:
    """Not an editing tool : the teams are written once."""
    backfill_teams(gdir, "relais", [], MEMBERS)
    before = _pgn(gdir)
    with pytest.raises(ChessboardError, match="already has its teams"):
        backfill_teams(gdir, "relais", [], ["admin"])
    with pytest.raises(ChessboardError, match="already has its teams"):
        backfill_teams(gdir, "relais", ["stephane"], [], dry_run=True)
    assert _pgn(gdir) == before
    assert len(_journal(gdir).read_text(encoding="utf-8").splitlines()) == 1


@pytest.mark.parametrize(
    "teams", [{"black_team": ["junior"]}, {"white_team": ["junior"]}], ids=["noirs", "blancs"]
)
def test_a_game_created_with_a_team_is_refused(gdir: Path, teams: dict) -> None:
    GameState.create(
        GameMetadata(game_id="neuve", white=TEAM, black="stephane", **teams),
        games_dir=gdir,
    )
    with pytest.raises(ChessboardError, match="already has its teams"):
        backfill_teams(gdir, "neuve", ["admin"], [])


def test_the_game_is_locked_while_its_teams_are_written(
    gdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Under the lock of the moves : no move written between the read and the save."""
    import contextlib

    from palamede_chessboard import clock_moves

    seen = []

    @contextlib.contextmanager
    def recording(game_id: str, games_dir: Path):
        seen.append(("lock", game_id))
        yield
        seen.append(("unlock", GameState.load(game_id, games_dir=games_dir).metadata.black_team))

    monkeypatch.setattr(clock_moves, "game_lock", recording)
    backfill_teams(gdir, "relais", [], MEMBERS)
    assert seen == [("lock", "relais"), ("unlock", MEMBERS)]


@pytest.mark.parametrize(
    ("white_team", "black_team", "error"),
    [
        ([], [], "no team given"),
        ([], ["leader", "inconnu"], "unknown player 'inconnu'"),
        ([], ["stephane", "leader"], "both sides"),
        ([], [TEAM], "is a team, not a player"),
        ([], ["leader", "leader"], "twice"),
    ],
)
def test_members_pass_the_checks_of_creation(
    gdir: Path, white_team: list, black_team: list, error: str
) -> None:
    before = _pgn(gdir)
    with pytest.raises(ChessboardError, match=error):
        backfill_teams(gdir, "relais", white_team, black_team)
    assert _pgn(gdir) == before
    assert not _journal(gdir).exists()


def test_unknown_game_leaves_no_lock(gdir: Path) -> None:
    with pytest.raises(GameNotFoundError):
        backfill_teams(gdir, "absente", [], MEMBERS)
    assert not (gdir / "absente.pgn.lock").exists()


def test_cli_backfill(gdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["relay-teams", "relais", "--black", "leader, admin,junior"]
    assert cli_players.main([*argv, "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert GameState.load("relais", games_dir=gdir).metadata.black_team == []
    assert cli_players.main(argv) == 0
    assert json.loads(capsys.readouterr().out)["black_team"] == MEMBERS
    assert cli_players.main(argv) == 1
    assert "already has its teams" in capsys.readouterr().err
