"""PGN text of a game : headers read into metadata, movetext written out.

Split from :mod:`palamede_chessboard.chessboard_state` so the PGN format
(headers, movetext, termination comment) lives in one place, apart from
the game rules. :class:`~palamede_chessboard.chessboard_state.GameState`
calls :func:`metadata_from_headers` on load and :func:`pgn_text` on save.
"""

from __future__ import annotations

import chess

from palamede_chessboard.metadata import GameMetadata, TimeControl


def _movetext(san_history: list[str], root: chess.Board) -> str:
    """Number ``san_history`` from the root position (issue #36).

    A standard game gives ``1. e4 e5 2. Nf3``. A game started from a FEN
    keeps its own move number, and opens with ``N...`` when Black moves
    first, so the PGN stays valid for the viewer and external tools.
    """
    number = root.fullmove_number
    white_to_move = root.turn == chess.WHITE
    tokens: list[str] = []
    for san in san_history:
        if white_to_move:
            tokens.append(f"{number}. {san}")
        else:
            tokens.append(f"{number}... {san}" if not tokens else san)
            number += 1
        white_to_move = not white_to_move
    return " ".join(tokens)


def metadata_from_headers(game_id: str, headers: dict[str, str]) -> GameMetadata:
    """Build the :class:`GameMetadata` of ``game_id`` from its PGN headers."""
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
        time_control=TimeControl(format=headers.get("TimeControl", "unrated")),
        result=headers.get("Result", "*"),
        annotator=headers.get("Annotator") or None,
        opening=headers.get("Opening") or None,
        eco=headers.get("ECO") or None,
        draw_offer=draw_offer,
        termination_note=headers.get("Termination") or None,
        # Preserve the Rated header as-read : an imported PGN with no
        # Rated header stays rated=False after a round-trip, so loading
        # then saving it will NOT suddenly stamp it as rated.
        rated=rated_raw == "true",
    )


def pgn_text(metadata: GameMetadata, root: chess.Board, san_history: list[str]) -> str:
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
    body = f"{_movetext(san_history, root)} {tail}" if san_history else tail
    return "\n".join(lines) + "\n\n" + body + "\n"
