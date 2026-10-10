"""Tests for :mod:`palamede_chessboard.chess_elo.algorithm` (pure math)."""

from __future__ import annotations

import pytest

from palamede_chessboard.chess_elo import (
    K_ESTABLISHED,
    K_PROVISIONAL,
    PROVISIONAL_GAMES,
    PlayerRating,
    apply_single_update,
    expected_score,
    k_factor,
)


class TestExpectedScore:
    def test_equal_ratings_half(self) -> None:
        assert expected_score(1500, 1500) == pytest.approx(0.5)

    def test_higher_rating_more_than_half(self) -> None:
        assert expected_score(1600, 1500) > 0.5

    def test_sum_to_one(self) -> None:
        """Expected scores sum to 1 — zero-sum property."""
        a, b = 1623, 1487
        assert expected_score(a, b) + expected_score(b, a) == pytest.approx(1.0)

    def test_known_400_point_gap(self) -> None:
        """FIDE : a 400-point gap gives the higher player ~0.909."""
        assert expected_score(1900, 1500) == pytest.approx(10 / 11, abs=1e-6)


class TestKFactor:
    def test_provisional(self) -> None:
        assert k_factor(0) == K_PROVISIONAL
        assert k_factor(PROVISIONAL_GAMES - 1) == K_PROVISIONAL

    def test_boundary(self) -> None:
        """CD rule : K=40 for the first 30 games, K=20 from the 31st."""
        assert k_factor(PROVISIONAL_GAMES) == K_ESTABLISHED

    def test_established(self) -> None:
        assert k_factor(100) == K_ESTABLISHED


class TestApplySingleUpdate:
    def test_equal_rating_win(self) -> None:
        w = PlayerRating(rating=1500, games=0)
        b = PlayerRating(rating=1500, games=0)
        new_w, new_b, kw, kb = apply_single_update(w, b, white_score=1.0)
        # Expected score 0.5, so delta = K * 0.5 = 20 on both sides at K=40.
        assert new_w.rating == 1520
        assert new_b.rating == 1480
        assert new_w.games == 1
        assert new_b.games == 1
        assert kw == kb == K_PROVISIONAL

    def test_equal_rating_draw(self) -> None:
        w = PlayerRating(rating=1500, games=0)
        b = PlayerRating(rating=1500, games=0)
        new_w, new_b, _, _ = apply_single_update(w, b, white_score=0.5)
        assert new_w.rating == 1500
        assert new_b.rating == 1500

    def test_zero_sum_total_same_k(self) -> None:
        """With equal K-factors, pre- vs post-game total ratings are
        zero-sum within rounding. Mixed K breaks this (one player gains
        more than the other loses, by design — see
        :meth:`test_mixed_k_is_not_zero_sum`)."""
        w = PlayerRating(rating=1623, games=5)
        b = PlayerRating(rating=1487, games=10)  # both provisional → K=40
        new_w, new_b, _, _ = apply_single_update(w, b, white_score=0.0)
        delta = (new_w.rating + new_b.rating) - (w.rating + b.rating)
        # Independent per-player rounding can introduce ±1, but no systematic drift.
        assert abs(delta) <= 1

    def test_mixed_k_is_not_zero_sum(self) -> None:
        """When one player is provisional (K=40) and the other established
        (K=20), the system is deliberately NOT zero-sum : the provisional
        rating swings twice as much per game, so the pool total can drift.
        Documented acceptance, not a bug."""
        w = PlayerRating(rating=1623, games=15)  # provisional
        b = PlayerRating(rating=1487, games=42)  # established
        new_w, _, kw, kb = apply_single_update(w, b, white_score=0.0)
        assert kw != kb  # by construction
        # The provisional player (W) is the one losing — K=40 → bigger drop
        # than the K=20 recipient's gain.
        drop_w = w.rating - new_w.rating
        assert drop_w > K_ESTABLISHED  # a provisional loss can't fit inside 20pt

    def test_k_mix_provisional_established(self) -> None:
        """Each player's K is picked from their OWN games count — CD rule 2."""
        w = PlayerRating(rating=1500, games=PROVISIONAL_GAMES)  # established
        b = PlayerRating(rating=1500, games=0)  # provisional
        _, _, kw, kb = apply_single_update(w, b, white_score=1.0)
        assert kw == K_ESTABLISHED
        assert kb == K_PROVISIONAL
