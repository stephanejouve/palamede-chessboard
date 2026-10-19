"""Transport Streamable HTTP sans état (BT-008, #28).

Vérifie : ``/healthz``, ``initialize`` puis ``tools/list`` et ``tools/call``
dans des requêtes **indépendantes**, sans identifiant de session (un client
qui revient d'une veille ou après un redémarrage du conteneur n'a rien à
renégocier), et une vraie session cliente du SDK MCP contre un uvicorn réel.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest
from starlette.testclient import TestClient

from palamede_chessboard import mcp_chessboard_server as srv

HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(method: str, params: dict | None = None, id_: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def _payload(resp) -> dict:
    """Réponse JSON directe ou flux SSE à un seul événement ``data:``."""
    if resp.headers.get("content-type", "").startswith("application/json"):
        return resp.json()
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    raise AssertionError(f"pas de payload JSON-RPC : {resp.text[:200]!r}")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    with TestClient(srv.build_http_app()) as c:
        yield c


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200 and r.text == "ok"


def test_stateless_requests_need_no_session(client):
    init = client.post(
        "/mcp/",
        headers=HEADERS,
        json=_rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        ),
    )
    assert init.status_code == 200
    assert "mcp-session-id" not in {k.lower() for k in init.headers}
    assert _payload(init)["result"]["serverInfo"]["name"] == "mcp-chessboard"
    # Nouvelle requête, sans en-tête de session : elle doit passer telle quelle.
    tools = client.post("/mcp/", headers=HEADERS, json=_rpc("tools/list", id_=2))
    assert tools.status_code == 200
    names = {t["name"] for t in _payload(tools)["result"]["tools"]}
    assert {"chess_create_game", "chess_list_games", "chess_resign"} <= names


def test_parse_args_defaults_and_env(monkeypatch):
    monkeypatch.delenv(srv.TRANSPORT_ENV, raising=False)
    a = srv._parse_args([])
    assert (a.transport, a.host, a.port) == ("stdio", "127.0.0.1", srv.DEFAULT_HTTP_PORT)
    monkeypatch.setenv(srv.TRANSPORT_ENV, "http")
    monkeypatch.setenv(srv.HTTP_HOST_ENV, "0.0.0.0")
    monkeypatch.setenv(srv.HTTP_PORT_ENV, "9001")
    a = srv._parse_args([])
    assert (a.transport, a.host, a.port) == ("http", "0.0.0.0", 9001)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_sdk_client_create_play_resign_over_http(tmp_path, monkeypatch):
    """Client MCP du SDK contre un uvicorn réel : création, 4 coups, abandon (critère 2)."""
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    monkeypatch.setenv("MCP_CHESSBOARD_GAMES_DIR", str(tmp_path))
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(srv.build_http_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)

    async def scenario() -> list[str]:
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp/") as (r, w, *_):
            async with ClientSession(r, w) as s:
                await s.initialize()

                async def call(name: str, args: dict) -> dict:
                    res = await s.call_tool(name, args)
                    assert not res.isError, res.content[0].text
                    body = json.loads(res.content[0].text)
                    assert body.get("status") != "error", body
                    return body

                gid = "http-maquette-1"
                await call(
                    "chess_create_game",
                    {"game_id": gid, "white": "admin", "black": "leader", "arbiter": "leader-bot"},
                )
                for by, mv in (
                    ("admin", "e4"),
                    ("leader", "e5"),
                    ("admin", "Nf3"),
                    ("leader", "Nc6"),
                ):
                    await call("chess_play_move", {"game_id": gid, "move_san": mv, "by": by})
                await call("chess_resign", {"game_id": gid, "by": "admin"})
                listed = await call("chess_list_games", {})
                assert gid in json.dumps(listed)
                return [json.dumps(listed)]

    try:
        out = asyncio.run(scenario())
        assert out
    finally:
        server.should_exit = True
        t.join(timeout=5)
