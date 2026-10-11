"""Tests for `palamede_chessboard.mcp_chessboard_server` — chess_get_elo tool.

Carved out of the pre-split 1471l test_mcp_chessboard_server.py.
Shared `games_dir` fixture lives in tests/mcp/conftest.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from palamede_chessboard.mcp_chessboard_server import call_tool
from tests.mcp.conftest import _decode


class TestGetEloTool:
    """``chess_get_elo`` : lecture + rebuild des ratings depuis les PGNs."""

    async def _create_server_game(
        self,
        *,
        game_id: str,
        white: str,
        black: str,
        resign_by: str,
    ) -> None:
        """Create a game then resign it so Site/Result qualify for Elo."""
        await call_tool(
            "chess_create_game",
            {
                "game_id": game_id,
                "white": white,
                "black": black,
                "site": "Chas, Tiresias",
            },
        )
        await call_tool("chess_resign", {"game_id": game_id, "by": resign_by})

    @pytest.mark.asyncio
    async def test_empty_dir_rebuilds_to_empty_board(self, games_dir: Path) -> None:
        body = _decode(await call_tool("chess_get_elo", {}))
        assert body["status"] == "ok"
        assert body["cache_hit"] is False
        assert body["provisional_games"] == 30
        assert body["count"] == 0
        assert body["players"] == []

    @pytest.mark.asyncio
    async def test_rebuild_produces_two_mirrored_updates(self, games_dir: Path) -> None:
        """Un match 1-0 : White gagne des points, Black en perd autant (même K)."""
        await self._create_server_game(
            game_id="g1", white="junior", black="leader", resign_by="black"
        )
        body = _decode(await call_tool("chess_get_elo", {}))
        assert body["status"] == "ok"
        assert body["count"] == 2
        names = {p["name"] for p in body["players"]}
        assert names == {"junior", "leader"}
        rating_by_name = {p["name"]: p for p in body["players"]}
        assert rating_by_name["junior"]["rating"] > 1500
        assert rating_by_name["leader"]["rating"] < 1500
        delta_white = rating_by_name["junior"]["rating"] - 1500
        delta_black = 1500 - rating_by_name["leader"]["rating"]
        assert delta_white == delta_black  # zero-sum, même K provisionnel

    @pytest.mark.asyncio
    async def test_provisional_flag_true_on_first_game(self, games_dir: Path) -> None:
        """Un seul match joué → les 2 joueurs sortent avec provisional=True."""
        await self._create_server_game(game_id="g1", white="a", black="b", resign_by="black")
        body = _decode(await call_tool("chess_get_elo", {}))
        for p in body["players"]:
            assert p["provisional"] is True
            assert p["games"] == 1

    @pytest.mark.asyncio
    async def test_provisional_flag_flips_at_threshold(self, games_dir: Path) -> None:
        """CD nit 2026-10-03 : vrai test de bascule provisional → established.

        Un joueur ``hero`` enchaîne PROVISIONAL_GAMES wins contre des ghosts
        distincts, le flag passe à False dès que ``games`` atteint
        ``PROVISIONAL_GAMES``.
        """
        from palamede_chessboard.chess_elo import PROVISIONAL_GAMES

        for i in range(PROVISIONAL_GAMES):
            await self._create_server_game(
                game_id=f"warmup-{i:02d}",
                white="hero",
                black=f"ghost-{i:02d}",
                resign_by="black",
            )
        body = _decode(await call_tool("chess_get_elo", {"player": "hero"}))
        hero = body["players"][0]
        assert hero["games"] == PROVISIONAL_GAMES
        assert hero["provisional"] is False

    @pytest.mark.asyncio
    async def test_player_filter_narrows_result(self, games_dir: Path) -> None:
        await self._create_server_game(
            game_id="g1", white="junior", black="leader", resign_by="black"
        )
        body = _decode(await call_tool("chess_get_elo", {"player": "junior"}))
        assert body["count"] == 1
        assert body["players"][0]["name"] == "junior"

    @pytest.mark.asyncio
    async def test_player_filter_unknown_returns_empty(self, games_dir: Path) -> None:
        await self._create_server_game(game_id="g1", white="a", black="b", resign_by="black")
        body = _decode(await call_tool("chess_get_elo", {"player": "ghost"}))
        assert body["count"] == 0
        assert body["players"] == []

    @pytest.mark.asyncio
    async def test_player_filter_normalised_match(self, games_dir: Path) -> None:
        """CD follow-up PR #282 2026-10-03 : filtre player passé par
        normalize_identity, donc Leader / LEADER / devleader doivent tous
        matcher la fiche canonique ``leader``."""
        await self._create_server_game(
            game_id="g1", white="junior", black="leader", resign_by="black"
        )
        for raw in ("Leader", "LEADER", " leader ", "devleader"):
            body = _decode(await call_tool("chess_get_elo", {"player": raw}))
            assert body["count"] == 1, f"{raw!r} did not match"
            assert body["players"][0]["name"] == "leader"

    @pytest.mark.asyncio
    async def test_include_history_returns_updates(self, games_dir: Path) -> None:
        await self._create_server_game(
            game_id="g1", white="junior", black="leader", resign_by="black"
        )
        body = _decode(await call_tool("chess_get_elo", {"include_history": True}))
        assert "history" in body
        assert len(body["history"]) == 1
        h = body["history"][0]
        assert h["game_id"] == "g1"
        assert h["white"] == "junior"
        assert h["black"] == "leader"
        assert h["white_score"] == 1.0
        assert h["white_delta"] > 0
        assert h["black_delta"] < 0

    @pytest.mark.asyncio
    async def test_default_omits_history(self, games_dir: Path) -> None:
        await self._create_server_game(game_id="g1", white="a", black="b", resign_by="black")
        body = _decode(await call_tool("chess_get_elo", {}))
        assert "history" not in body

    @pytest.mark.asyncio
    async def test_rebuild_sorted_by_rating_desc(self, games_dir: Path) -> None:
        """Winner ends up above loser dans la liste players."""
        await self._create_server_game(
            game_id="g1", white="winner", black="loser", resign_by="black"
        )
        body = _decode(await call_tool("chess_get_elo", {}))
        assert body["players"][0]["name"] == "winner"
        assert body["players"][1]["name"] == "loser"

    @pytest.mark.asyncio
    async def test_cache_only_missing_returns_null_board(self, games_dir: Path) -> None:
        """``cache_only=true`` sans cache préalable : réponse vide, pas d'erreur."""
        body = _decode(await call_tool("chess_get_elo", {"cache_only": True}))
        assert body["status"] == "ok"
        assert body["cache_hit"] is False
        assert body["count"] == 0

    @pytest.mark.asyncio
    async def test_rebuild_writes_cache_side_effect(self, games_dir: Path) -> None:
        """Un rebuild non-cache-only crée ``ratings.json`` dans ``games_dir``."""
        await self._create_server_game(
            game_id="g1", white="junior", black="leader", resign_by="black"
        )
        _ = _decode(await call_tool("chess_get_elo", {}))
        cache_path = games_dir / "ratings.json"
        assert cache_path.exists()
        # Second appel en cache_only doit maintenant hit.
        body = _decode(await call_tool("chess_get_elo", {"cache_only": True}))
        assert body["cache_hit"] is True
        assert body["count"] == 2

    @pytest.mark.asyncio
    async def test_imported_games_excluded_from_elo(self, games_dir: Path) -> None:
        """Un PGN sans header [Rated "true"] (import) ne doit pas peser."""
        await self._create_server_game(game_id="server", white="a", black="b", resign_by="black")
        # Simulate an imported PGN by writing one directly with foreign Site.
        imported = games_dir / "lichess-import.pgn"
        imported.write_text(
            '[Event "Lichess"]\n'
            '[Site "lichess.org"]\n'
            '[White "GM-A"]\n'
            '[Black "GM-B"]\n'
            '[Result "1-0"]\n\n1. e4 e5 1-0\n',
            encoding="utf-8",
        )
        body = _decode(await call_tool("chess_get_elo", {}))
        names = {p["name"] for p in body["players"]}
        assert names == {"a", "b"}
        assert "GM-A" not in names
        assert "GM-B" not in names
