"""Which games the bot plays, and one round of play.

Rules (owner and Leader, #93, 29/10) — Maia plays against humans, never as
a source of moves for the agents :

* only the ``bot-maia-<elo>`` accounts whose token the bot holds, and only
  when that account is **to move** : the server checks the token too ;
* only **unrated** games : a rated game is skipped and logged ;
* only against an opponent listed as **human** in the bot's configuration
  (``--humans``) : never an agent, never another bot, never an unknown
  account. Fail-closed : with no human listed, the bot plays nobody ;
* a game is never played by two of its levels : bot against bot is skipped.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from palamede_maia import viewer
from palamede_maia.engine import LEVELS

log = logging.getLogger(__name__)

BOT_ID = re.compile(r"bot-maia-(\d{4})")


def level_of(player: str) -> int | None:
    """Elo of a ``bot-maia-<elo>`` account with Maia weights, else ``None``."""
    match = BOT_ID.fullmatch(player)
    if match is None:
        return None
    elo = int(match.group(1))
    return elo if elo in LEVELS else None


@dataclass
class Bot:
    viewer_url: str
    tokens: dict[str, str]
    humans: frozenset[str]
    move_of: Callable[[int, str], str | None]
    _skipped: set[str] = field(default_factory=set)

    def _skip_once(self, game_id: str, why: str) -> None:
        if game_id not in self._skipped:
            self._skipped.add(game_id)
            log.info("%s skipped : %s", game_id, why)

    def turn_of(self, game: dict) -> str | None:
        """The bot account to move in ``game`` if the rules allow it, else ``None``."""
        game_id = str(game.get("game_id", ""))
        side = game.get("turn")
        if (
            game.get("is_game_over")
            or game.get("result", "*") != "*"
            or side not in ("white", "black")
        ):
            return None
        mover = str(game.get(side, ""))
        if mover not in self.tokens:
            return None
        opponent = str(game.get("black" if side == "white" else "white", ""))
        if game.get("rated") is not False:
            self._skip_once(game_id, "rated game, Maia only plays unrated ones")
            return None
        if opponent not in self.humans:
            self._skip_once(game_id, f"opponent {opponent!r} is not a listed human")
            return None
        if level_of(mover) is None:
            self._skip_once(game_id, f"{mover!r} has no Maia level")
            return None
        return mover

    def play_round(self) -> int:
        """One pass over the games in progress ; the number of moves played."""
        games = viewer.games_in_progress(self.viewer_url)
        if games is None:
            return 0
        played = 0
        for game in games:
            mover = self.turn_of(game)
            if mover is None:
                continue
            if self._play(str(game["game_id"]), mover):
                played += 1
        return played

    def _play(self, game_id: str, mover: str) -> bool:
        fen = viewer.last_fen(self.viewer_url, game_id)
        if fen is None:
            return False
        elo = level_of(mover)
        assert elo is not None
        uci = self.move_of(elo, fen)
        if uci is None:
            return False
        status = viewer.play(self.viewer_url, game_id, uci, self.tokens[mover])
        if status == 200:
            log.info("%s : %s plays %s", game_id, mover, uci)
            return True
        # 403 / 409 / 422 : the position moved meanwhile, or the server refuses ;
        # the next round reads the game again.
        log.warning("%s : move %s of %s refused (HTTP %s)", game_id, uci, mover, status)
        return False
