"""Player accounts and API tokens (#34). See :mod:`.registry`."""

from palamede_chessboard.players.registry import (
    KINDS,
    RegistryError,
    add_player,
    issue_token,
    list_players,
    registry_path,
    revoke_token,
    verify_token,
)

__all__ = [
    "KINDS",
    "RegistryError",
    "add_player",
    "issue_token",
    "list_players",
    "registry_path",
    "revoke_token",
    "verify_token",
]
