"""What a player or an agent reads when an operation is refused (#322, #332).

One operation, the same refusals, whatever the medium (owner, #328) : the
domain raises these texts, the viewer's HTTP API returns them as ``detail``,
the MCP tools as ``reason`` next to a stable ``code``. French, French
notation (R, D, T, F, C), no UCI, no ply, no internal detail : the technical
reason goes to the log. A program tests the code (``X-Refusal-Code`` header
on HTTP, ``code`` on MCP), never the text.

``viewer/refusals.py`` carries the same texts for the viewer today ; the
parity tests of #332 keep the two equal until it imports from here.
"""

from __future__ import annotations

import chess

#: English SAN piece letters → French ones. SAN writes pieces in upper case
#: only (files are lower case), so a plain translation is exact.
_FRENCH_LETTERS = str.maketrans("KQRBN", "RDTFC")
_FRENCH_PIECE = {
    chess.KING: "R",
    chess.QUEEN: "D",
    chess.ROOK: "T",
    chess.BISHOP: "F",
    chess.KNIGHT: "C",
    chess.PAWN: "",
}

#: A move refused (``play_move``).
UNREADABLE = "Coup refusé : coup illisible."
NO_MOVE = "Coup refusé : aucun coup reçu."
GAME_OVER = "Coup refusé : la partie est terminée."

#: A resignation or a draw action refused (``resign``, ``*_draw``).
END_GAME_OVER = "La partie est terminée."
END_OPPONENT_OFFERED = "Votre adversaire propose déjà la nulle : acceptez-la ou refusez-la."
END_NO_OFFER = "Aucune proposition de nulle en cours."
END_OWN_OFFER = "C'est votre propre proposition : votre adversaire doit répondre."
END_REFUSED = "Action refusée dans l'état actuel de la partie."

#: Who may act : the code of :func:`players.observe.authorize_move`, the HTTP
#: status and the text. The viewer's ``X-Refusal-Code`` and the MCP ``code``
#: carry the key.
MOVE_REFUSALS: dict[str, tuple[int, str]] = {
    "UNAUTHENTICATED": (401, "Jeton de joueur requis ou invalide (bouton 🔑)"),
    "NOT_YOUR_TURN": (403, "Ce n'est pas votre trait"),
    "NOT_A_PLAYER": (403, "Vous ne jouez pas dans cette partie"),
    "REGISTRY_UNREADABLE": (503, "Registre des joueurs illisible : coup refusé"),
}


def french_san(san: str) -> str:
    """``Rf1`` → ``Tf1``, ``exd8=Q+`` → ``exd8=D+`` ; castling unchanged."""
    return san.translate(_FRENCH_LETTERS)


def attempted(board: chess.Board, move: chess.Move) -> str:
    """The move a player tried, said the French way even when illegal.

    The piece on the start square and the target square (``Tf1``, ``e5``) ;
    no piece there : « aucune pièce en h1 ».
    """
    piece = board.piece_at(move.from_square)
    if piece is None:
        return f"aucune pièce en {chess.square_name(move.from_square)}"
    return _FRENCH_PIECE[piece.piece_type] + chess.square_name(move.to_square)


def not_possible(what: str) -> str:
    """« Coup refusé : Tf1 n'est pas possible dans cette position. »"""
    if what.startswith("aucune pièce"):
        return f"Coup refusé : {what}."
    return f"Coup refusé : {what} n'est pas possible dans cette position."


#: A **legal** move written wrong (review Leader #340) : the strict arbiter
#: (:func:`chess_arbiter.validate_san_strict`) refuses it for its notation,
#: and « n'est pas possible » would send the agent to another move. Keyed by
#: ``ValidationResult.kind`` : what is wrong, then what to write when the
#: arbiter has it ; ``{san}`` what was sent, ``{fix}`` what to write, both in
#: French notation. Without a ``fix`` the first part stands alone : a notation
#: refusal never says « n'est pas possible » (reviews Leader and Admin #340).
NOTATION = {
    "ambiguous": ("{san} est ambigu : précisez la case de départ", " ({fix})"),
    "capture": ("{san} ne prend rien", " : écrivez {fix}"),
    "no_capture": ("{san} prend une pièce", " : écrivez {fix}"),
    "check": ("{san} ne donne pas échec", " : écrivez {fix}"),
    "no_check": ("{san} donne échec", " : écrivez {fix}"),
    "mate": ("{san} n'est pas mat", " : écrivez {fix}"),
    "no_mate": ("{san} est mat", " : écrivez {fix}"),
}


def move_refused(kind: str, san: str, suggestion: str = "") -> str:
    """The text of a refused SAN : by its notation when the move is legal, else :func:`not_possible`."""
    entry = NOTATION.get(kind)
    if entry is None:
        return not_possible(french_san(san))
    wrong, fix = entry
    template = wrong + fix if suggestion else wrong
    return "Coup refusé : " + template.format(san=french_san(san), fix=french_san(suggestion)) + "."


def refusal_text(code: str | None) -> str:
    """The text of an identity refusal code ; « Coup refusé » for an unknown one."""
    return MOVE_REFUSALS.get(code or "", (403, "Coup refusé"))[1]
