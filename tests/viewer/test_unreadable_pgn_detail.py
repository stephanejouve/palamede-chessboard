"""A PGN damaged on the server is said by its game, not its path (#107)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_positions_of_an_unreadable_pgn_say_no_path(client: TestClient, games_dir: Path) -> None:
    (games_dir / "broken.pgn").write_text("", encoding="utf-8")
    r = client.get("/api/positions?game_id=broken")
    assert r.status_code == 400
    assert r.json() == {"detail": "PGN of broken is unreadable"}
    assert str(games_dir) not in r.text
