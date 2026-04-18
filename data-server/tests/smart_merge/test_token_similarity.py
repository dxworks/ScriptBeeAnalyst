"""Tests for the corroboration guard in compute_best_similarity."""
import pytest

from src.smart_merge.token_similarity import compute_best_similarity
from src.smart_merge.types import SimilarityType


class TestCorroborationGuard:
    """Verify that name-only matches without email/login corroboration
    are blocked for short names but allowed for distinctive long names."""

    # ── IDENTICAL without corroboration ─────────────────────────────────

    def test_single_token_identical_no_corroboration_blocked(self):
        result = compute_best_similarity("Lee", None, None, "Lee", None, None)
        assert result.type == SimilarityType.DIFFERENT
        assert result.strength == 0

    def test_two_token_identical_no_corroboration_blocked(self):
        result = compute_best_similarity("John Lee", None, None, "John Lee", None, None)
        assert result.type == SimilarityType.DIFFERENT
        assert result.strength == 0

    def test_three_token_identical_no_corroboration_allowed(self):
        result = compute_best_similarity(
            "Prabhjyot Singh Bhatia", None, None,
            "Prabhjyot Singh Bhatia", None, None,
        )
        assert result.type == SimilarityType.IDENTICAL
        assert result.strength == 3

    # ── IDENTICAL with corroboration (should still work) ────────────────

    def test_single_token_identical_with_email_corroboration(self):
        result = compute_best_similarity(
            "Lee", "lee@example.com", None,
            "Lee", "lee@example.com", None,
        )
        assert result.type != SimilarityType.DIFFERENT

    def test_single_token_identical_with_login_corroboration(self):
        result = compute_best_similarity(
            "Lee", None, "lee123",
            "Lee", None, "lee123",
        )
        assert result.type != SimilarityType.DIFFERENT

    def test_two_token_identical_with_email_corroboration(self):
        result = compute_best_similarity(
            "John Lee", "john.lee@company.com", None,
            "John Lee", "john.lee@company.com", None,
        )
        assert result.type != SimilarityType.DIFFERENT

    # ── SIMILAR without corroboration (regression: existing guard) ──────

    def test_similar_partial_overlap_no_corroboration_blocked(self):
        """Partial token overlap should be blocked — must not create edges.
        _check_similarity returns DIFFERENT(1) here (only 1 token matched),
        and upsert_undirected_edge requires strength > 0, but the type
        being DIFFERENT means the guard would catch it if it were SIMILAR."""
        result = compute_best_similarity(
            "Lee moon soo", None, None,
            "ChanHo Lee", None, None,
        )
        assert result.type == SimilarityType.DIFFERENT

    # ── Cross-field corroboration ───────────────────────────────────────

    def test_single_token_identical_with_cross_login_email_corroboration(self):
        """Login matching email prefix counts as corroboration."""
        result = compute_best_similarity(
            "Lee", None, "moonlee",
            "Lee", "moonlee@company.com", None,
        )
        assert result.type != SimilarityType.DIFFERENT

    # ── Completely different names (sanity check) ──────────────────────

    def test_different_names_no_match(self):
        result = compute_best_similarity("Alice", None, None, "Bob", None, None)
        assert result.type == SimilarityType.DIFFERENT
        assert result.strength == 0


class TestPerFieldCorroboration:
    """Corroboration must come from a DIFFERENT field than the one that
    produced the best signal. A field cannot corroborate itself."""

    def test_login_similar_alone_does_not_self_corroborate(self):
        """Two logins sharing a 3-char prefix produce SIMILAR on the login
        field only. With no name or email agreement, the match must be
        blocked — a field cannot corroborate itself."""
        result = compute_best_similarity(
            "Alpha", None, "devuser1",
            "Bravo", None, "devuser2",
        )
        assert result.type == SimilarityType.DIFFERENT

    def test_name_identical_plus_login_similar_crosses_fields(self):
        """IDENTICAL(2) name + SIMILAR login is genuine cross-field
        corroboration — allow."""
        result = compute_best_similarity(
            "John Lee", None, "johnlee",
            "John Lee", None, "johnlee99",
        )
        assert result.type != SimilarityType.DIFFERENT

    def test_single_token_similar_always_blocked(self):
        """Even with a SIMILAR name match across very different single
        tokens, we refuse to emit SIMILAR — partial overlap of single
        tokens is noise."""
        # Force SIMILAR on the name path: one-token names with shared 3-char prefix.
        result = compute_best_similarity(
            "alexander", None, None,
            "alex", None, None,
        )
        assert result.type == SimilarityType.DIFFERENT


class TestSingleTokenGuard:
    def test_single_token_identical_remains_blocked_without_cross_field(self):
        # name IDENTICAL(1), no email, no login → no cross-field corroboration
        result = compute_best_similarity("Lee", None, None, "Lee", None, None)
        assert result.type == SimilarityType.DIFFERENT
