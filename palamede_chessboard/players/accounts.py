"""Deleting an account (#362) : only one that no game names.

A deleted account leaves the registry with its tokens, and its
preferences go : its name is free again for a sign-up. A name found in a
game's headers stays the past player's (#351 §3) : the account is then
not deleted, it may be disabled (:func:`~.registry.disable_player`).
"""

from __future__ import annotations

from pathlib import Path

from palamede_chessboard.player_prefs import forget_player
from palamede_chessboard.players import registry
from palamede_chessboard.players.signup import names_in_history


class AccountHasGames(registry.RegistryError):
    """The account's name is in a game : not deleted, its history kept."""


def delete_account(games_dir: Path, raw_id: object, *, dry_run: bool = False) -> str:
    """Delete ``raw_id`` if no game names it ; its ``player_id``.

    :class:`AccountHasGames` if a game does, :class:`~.registry.RegistryError`
    for an unknown account ; ``OSError`` when a game cannot be read (the
    account is then kept : the game may name it). ``dry_run`` checks only.
    """
    player_id = registry.player_id_of(raw_id)
    if player_id not in registry.load(games_dir)["players"]:
        raise registry.RegistryError(f"unknown player {player_id!r}")
    if player_id in names_in_history(games_dir):
        raise AccountHasGames(f"{player_id!r} plays in a game : not deleted, disable it instead")
    if not dry_run:
        # Preferences first : if the registry write then fails, the account
        # is still there and the command can be run again.
        forget_player(games_dir, player_id)
        registry.remove_player(games_dir, player_id)
    return player_id
