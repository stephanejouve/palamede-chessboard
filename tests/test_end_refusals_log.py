"""A refused resignation or draw action keeps its technical reason in the log (review Admin #340).

The reader gets the French text of :mod:`palamede_chessboard.refusals` ; the log keeps what the
text no longer says : the action, why, the result and the ply.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from palamede_chessboard import refusals
from palamede_chessboard.chessboard_state import (
    GameMetadata,
    GameState,
    InvalidGameStateError,
    TimeControl,
)

GAME = "end-log"


def _game(tmp_path: Path) -> GameState:
    return GameState.create(
        GameMetadata(
            game_id=GAME, white="w", black="b", time_control=TimeControl(format="unrated")
        ),
        games_dir=tmp_path,
    )


@pytest.mark.parametrize(
    ("prepare", "action", "args", "text", "why"),
    [
        (lambda g: g.resign("white"), "resign", ("black",), refusals.END_GAME_OVER, "game over"),
        (
            lambda g: g.resign("white"),
            "offer_draw",
            ("white",),
            refusals.END_GAME_OVER,
            "game over",
        ),
        (
            lambda g: g.resign("white"),
            "agree_draw",
            ("white",),
            refusals.END_GAME_OVER,
            "game over",
        ),
        (
            lambda g: g.resign("white"),
            "decline_draw",
            ("white",),
            refusals.END_GAME_OVER,
            "game over",
        ),
        (
            lambda g: g.offer_draw("white"),
            "offer_draw",
            ("black",),
            refusals.END_OPPONENT_OFFERED,
            "opponent already offered",
        ),
        (lambda g: None, "agree_draw", ("white",), refusals.END_NO_OFFER, "no pending offer"),
        (lambda g: None, "decline_draw", ("white",), refusals.END_NO_OFFER, "no pending offer"),
        (
            lambda g: g.offer_draw("white"),
            "agree_draw",
            ("white",),
            refusals.END_OWN_OFFER,
            "own offer",
        ),
        (
            lambda g: g.offer_draw("white"),
            "decline_draw",
            ("white",),
            refusals.END_OWN_OFFER,
            "own offer",
        ),
    ],
)
def test_the_reader_gets_the_text_and_the_log_the_reason(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    prepare,
    action: str,
    args,
    text: str,
    why: str,
) -> None:
    game = _game(tmp_path)
    prepare(game)
    with caplog.at_level(logging.INFO, logger="palamede_chessboard.chessboard_state"):
        with pytest.raises(InvalidGameStateError) as refused:
            getattr(game, action)(*args)
    assert str(refused.value) == text
    assert why not in text
    [record] = [r for r in caplog.records if " rejected : " in r.getMessage()]
    assert record.levelno == logging.INFO
    assert record.getMessage().startswith(f"{GAME} : {action} rejected : {why} (result ")
