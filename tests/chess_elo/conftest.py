"""Shared fixtures for ``tests/chess_elo``.

The helpers write PGN files **textually** (no dependency on
``outillages.chessboard_state``) so this test suite is fully
self-contained inside ``palamede-chessboard``. Both finished games
(``_create_finished``) and in-progress server games (``_create_live``)
are supported ; imported-PGN variants (no ``Rated`` header) use
``_write_imported_pgn``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.chess_elo import cache as _cache_mod


@pytest.fixture(autouse=True)
def _cache_write_warned_reset():
    """Reset ``_cache_write_warned`` between tests.

    CD review palamede#13 (BT-006 V1) : the module-level flag that
    ensures a single warning per process otherwise leaks across tests
    — the second ``rebuild_and_persist_cache`` monkey-patched to raise
    would silently stay quiet. Autouse so every test gets a clean
    slate without opt-in. Yields control to the test, then resets on
    teardown as well (symmetric guard).
    """
    _cache_mod._cache_write_warned = False
    yield
    _cache_mod._cache_write_warned = False


def _write_pgn(
    games_dir: Path,
    *,
    game_id: str,
    headers: dict[str, str],
    movetext: str,
) -> Path:
    """Low-level writer : dump ``[Tag "value"]`` headers + movetext to a .pgn.

    ``headers`` ordering is preserved — dict insertion order (Python
    3.7+) matches the canonical PGN tag order expected by
    :mod:`chess.pgn` consumers (Event, Site, Date, Round, White, Black,
    Result, …).
    """
    games_dir.mkdir(parents=True, exist_ok=True)
    path = games_dir / f"{game_id}.pgn"
    lines = [f'[{tag} "{value}"]\n' for tag, value in headers.items()]
    text = "".join(lines) + "\n" + movetext + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _create_finished(
    tmp_path: Path,
    *,
    game_id: str,
    white: str,
    black: str,
    result: str,
    site: str = "Chas, Tiresias",
    date: str = "2026.09.30",
    round_: str = "1",
) -> Path:
    """Server game with ``[Rated "true"]`` and a terminal ``Result``.

    Equivalent to the pre-extraction ``_create_finished`` fixture that
    went through ``GameState.create`` + ``GameState.save`` ; here we
    write the textual PGN directly so the Elo tests don't depend on
    outillages. The movetext is a minimal legal line (``1. e4 e5``)
    plus the result token — ``collect_rated_games`` only cares about
    the headers, not the moves.
    """
    headers = {
        "Event": "Test",
        "Site": site,
        "Date": date,
        "Round": round_,
        "White": white,
        "Black": black,
        "Result": result,
        "Rated": "true",
    }
    movetext = f"1. e4 e5 {result}"
    return _write_pgn(tmp_path, game_id=game_id, headers=headers, movetext=movetext)


def _create_live(
    tmp_path: Path,
    *,
    game_id: str,
    white: str,
    black: str,
    site: str = "Chas, Tiresias",
    date: str = "2026.09.30",
) -> Path:
    """In-progress server game : ``[Rated "true"]`` but ``Result "*"``.

    Equivalent to a ``GameState.create`` without a termination call.
    Used to verify that live games are excluded from the Elo
    perimeter even when they carry the rated flag.
    """
    return _create_finished(
        tmp_path,
        game_id=game_id,
        white=white,
        black=black,
        result="*",
        site=site,
        date=date,
    )


def _write_imported_pgn(
    games_dir: Path,
    *,
    game_id: str,
    white: str,
    black: str,
    result: str,
    date: str = "2026.09.30",
) -> Path:
    """Drop a PGN that mimics an import (no ``Rated`` header).

    Stéphane arbitrage 2026-10-03 : the Elo perimeter is driven by
    ``[Rated "true"]``, not by ``Site``. An imported PGN carries no
    Rated header and must be filtered out even if its Site happens to
    match a server one.
    """
    headers = {
        "Event": "Import",
        "Site": "lichess.org",
        "Date": date,
        "Round": "-",
        "White": white,
        "Black": black,
        "Result": result,
    }
    movetext = f"1. e4 e5 {result}"
    return _write_pgn(games_dir, game_id=game_id, headers=headers, movetext=movetext)
