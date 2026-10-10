"""Tests for :mod:`palamede_chessboard.chess_elo.identity`."""

from __future__ import annotations

from palamede_chessboard.chess_elo import (
    CANONICAL_IDENTITIES,
    IDENTITY_ALIASES,
    normalize_identity,
)


class TestNormalizeIdentity:
    def test_lowercases_and_strips(self) -> None:
        assert normalize_identity("  Admin  ") == "admin"
        assert normalize_identity("LEADER") == "leader"
        assert normalize_identity("Junior") == "junior"
        assert normalize_identity("Claude-Desktop") == "claude-desktop"

    def test_devleader_maps_to_leader(self) -> None:
        """Stéphane arbitrage 2026-10-03 : ``devleader`` is Leader."""
        assert normalize_identity("devleader") == "leader"
        assert normalize_identity("DevLeader") == "leader"

    def test_empty_or_none_returns_empty(self) -> None:
        assert normalize_identity("") == ""
        assert normalize_identity("   ") == ""

    def test_unknown_name_falls_back_to_lower(self) -> None:
        """Unknown agents stay distinct from the canonical set, not swept
        into one bucket by accident."""
        assert normalize_identity("Unknown-Agent") == "unknown-agent"
        assert normalize_identity("tephra") == "tephra"
        assert normalize_identity("tephra") not in CANONICAL_IDENTITIES

    def test_canonical_identities_round_trip(self) -> None:
        for canonical in CANONICAL_IDENTITIES:
            assert normalize_identity(canonical) == canonical

    def test_alias_table_values_are_canonical(self) -> None:
        """Guard against a typo in :data:`IDENTITY_ALIASES` that would
        route an alias to a non-canonical bucket."""
        for value in IDENTITY_ALIASES.values():
            assert value in CANONICAL_IDENTITIES
