"""PGN text of a game : headers read into metadata, movetext written out.

Split from :mod:`palamede_chessboard.chessboard_state` so the PGN format
(headers, movetext, termination comment) lives in one place, apart from
the game rules. :class:`~palamede_chessboard.chessboard_state.GameState`
calls :func:`metadata_from_headers` on load and :func:`pgn_text` on save.
"""

from __future__ import annotations

import re

import chess

from palamede_chessboard.metadata import (
    CLOCK_FORMAT_RE,
    TIME_CONTROL_UNIT_TAG,
    GameMetadata,
    TimeControl,
)

#: A ``[%cmd …]`` of the PGN supplement, such as ``[%clk 0:14:58]``.
COMMAND_RE = re.compile(r"\[%[^\]]*\]")


def _clk(seconds: float) -> str:
    """``[%clk H:MM:SS]`` of the time left, in whole seconds (#151)."""
    left = max(0, int(seconds))
    return f"[%clk {left // 3600}:{left % 3600 // 60:02d}:{left % 60:02d}]"


def _movetext(
    san_history: list[str], root: chess.Board, clocks: list[float | None] | None = None
) -> str:
    """Number ``san_history`` from the root position (issue #36).

    A standard game gives ``1. e4 e5 2. Nf3``. A game started from a FEN
    keeps its own move number, and opens with ``N...`` when Black moves
    first, so the PGN stays valid for the viewer and external tools.
    ``clocks[i]``, when known, is written after move ``i`` as
    ``{ [%clk 0:14:58] }`` (#151) ; Black's move after it takes ``N...``.
    """
    clocks = clocks or []
    number = root.fullmove_number
    white_to_move = root.turn == chess.WHITE
    tokens: list[str] = []
    commented = False
    for i, san in enumerate(san_history):
        if white_to_move:
            tokens.append(f"{number}. {san}")
        else:
            tokens.append(f"{number}... {san}" if not tokens or commented else san)
            number += 1
        clock = clocks[i] if i < len(clocks) else None
        commented = clock is not None
        if clock is not None:
            tokens.append(f"{{ {_clk(clock)} }}")
        white_to_move = not white_to_move
    return " ".join(tokens)


def time_control_from_headers(headers: dict[str, str]) -> TimeControl:
    """The :class:`TimeControl` of a game, from its ``TimeControl`` header (#151).

    Seconds when the header carries :data:`TIME_CONTROL_UNIT_TAG`, else
    the minutes of the games written before. No header and ``-`` are
    ``unrated`` ; a label no clock reads (``40/7200:0+10``) is kept as is.
    """
    raw = headers.get("TimeControl")
    if raw is None or raw == "-":
        return TimeControl(format="unrated")
    match = CLOCK_FORMAT_RE.match(raw)
    if match is None or headers.get(TIME_CONTROL_UNIT_TAG) != "seconds":
        return TimeControl(format=raw)
    base, increment = (int(g) for g in match.groups())
    if base % 60 == 0:
        return TimeControl(format=f"{base // 60}+{increment}")
    # Not whole minutes : the seconds themselves, which the clock reads first.
    return TimeControl(format=raw, start_seconds=base, increment_seconds=increment)


#: The ``Termination`` values of the PGN standard. Any other value is the
#: readable note the games written before #151 kept in that header.
TERMINATIONS = frozenset(
    {
        "abandoned",
        "adjudication",
        "death",
        "emergency",
        "normal",
        "rules infraction",
        "time forfeit",
        "unterminated",
    }
)


def termination_from_pgn(
    headers: dict[str, str], final_comment: str
) -> tuple[str | None, str | None]:
    """``(termination, termination_note)`` of a game (#151).

    The note is the comment before the result. A game written before #151
    holds it in ``Termination`` instead : read back as a ``normal`` end,
    the only kind that carried a note (resignation, draw agreed).
    """
    raw = headers.get("Termination")
    if raw is None:
        return None, None
    if raw.lower() in TERMINATIONS:
        # The last move's ``[%clk]`` shares that comment : not the note.
        return raw, " ".join(COMMAND_RE.sub(" ", final_comment).split()) or None
    return "normal", raw


def metadata_from_headers(
    game_id: str, headers: dict[str, str], final_comment: str = ""
) -> GameMetadata:
    """Build the :class:`GameMetadata` of ``game_id`` from its PGN headers.

    ``final_comment`` : the comment before the result, where the
    termination note lives (#151).
    """
    termination, termination_note = termination_from_pgn(headers, final_comment)
    draw_offer_raw = headers.get("DrawOffer") or ""
    draw_offer: dict | None = None
    if ":" in draw_offer_raw:
        side, _, ply_str = draw_offer_raw.partition(":")
        if side in ("white", "black") and ply_str.isdigit():
            draw_offer = {"by": side, "ply": int(ply_str)}
    rated_raw = headers.get("Rated", "").strip().lower()
    return GameMetadata(
        game_id=game_id,
        event=headers.get("Event", ""),
        site=headers.get("Site", ""),
        date=headers.get("Date", ""),
        round=headers.get("Round", "1"),
        white=headers.get("White", ""),
        black=headers.get("Black", ""),
        arbiter=headers.get("Arbiter") or None,
        spectators=([s for s in headers.get("Spectators", "").split(",") if s]),
        white_team=[m for m in headers.get("WhiteTeam", "").split(",") if m],
        black_team=[m for m in headers.get("BlackTeam", "").split(",") if m],
        time_control=time_control_from_headers(headers),
        result=headers.get("Result", "*"),
        annotator=headers.get("Annotator") or None,
        opening=headers.get("Opening") or None,
        eco=headers.get("ECO") or None,
        draw_offer=draw_offer,
        termination_note=termination_note,
        termination=termination,
        # Preserve the Rated header as-read : an imported PGN with no
        # Rated header stays rated=False after a round-trip, so loading
        # then saving it will NOT suddenly stamp it as rated.
        rated=rated_raw == "true",
    )


def pgn_text(
    metadata: GameMetadata,
    root: chess.Board,
    san_history: list[str],
    clocks: list[float | None] | None = None,
) -> str:
    """The PGN of a game : headers, root position, movetext and result.

    Built in memory, so a header refused by #128 raises before any file
    is opened.
    """
    headers = metadata.pgn_headers_to_write()
    lines = [f'[{tag} "{value}"]' for tag, value in headers.items()]
    # Issue #36 : a game created from ``starting_fen`` must keep
    # its root position, or ``load`` (``game.board()``) restarts
    # from the initial position on the next MCP call.
    if root.fen() != chess.STARTING_FEN:
        lines.append('[SetUp "1"]')
        lines.append(f'[FEN "{root.fen()}"]')
    tail = metadata.result
    if metadata.termination_note:
        tail = f"{{{metadata.termination_note}}} {tail}"
    body = f"{_movetext(san_history, root, clocks)} {tail}" if san_history else tail
    return "\n".join(lines) + "\n\n" + body + "\n"
