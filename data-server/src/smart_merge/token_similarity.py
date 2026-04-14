"""
Token-based similarity engine for author name/email comparison.
Ported from the Chronos TokenSimilaritiesAlgorithm.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from src.smart_merge.types import Similarity, SimilarityType

DEFAULT_NOT_SIMILAR = "unknown"
SPLIT_RE = re.compile(r"\s|\.|-")

NAME_THRESHOLDS = (3, 3, 3)   # min_prefix, min_suffix, min_token_len
EMAIL_THRESHOLDS = (5, 5, 5)

GENERIC_EMAIL_DOMAINS = ["github", "users.noreply", "noreply"]


@dataclass
class _Token:
    value: str
    kind: SimilarityType = SimilarityType.DIFFERENT


def check_name_similarity(a: str, b: str) -> Similarity:
    return _check_similarity(a, b, NAME_THRESHOLDS)


def check_email_similarity(a: str, b: str) -> Similarity:
    return _check_similarity(a, b, EMAIL_THRESHOLDS)


def check_login_similarity(a: str, b: str) -> Similarity:
    """Compare logins/keys using name thresholds (they're short identifiers)."""
    return _check_similarity(a, b, NAME_THRESHOLDS)


def most_powerful_similarity(first: Similarity, second: Similarity) -> Similarity:
    rank = {
        SimilarityType.DIFFERENT: 1,
        SimilarityType.SIMILAR: 2,
        SimilarityType.SAME_AUTHOR: 2,
        SimilarityType.IDENTICAL: 3,
    }
    if rank[first.type] != rank[second.type]:
        return first if rank[first.type] > rank[second.type] else second
    return first if first.strength >= second.strength else second


def email_address_without_domain(email: str) -> str:
    """Extract the meaningful part of an email for comparison."""
    first_at = email.find("@")
    last_at = email.rfind("@")
    if first_at == -1:
        return email
    prefix = email[:first_at]
    if any(prefix.startswith(d) for d in GENERIC_EMAIL_DOMAINS):
        return email[last_at + 1:]
    return email[:last_at]


def compute_best_similarity(
    name_a: str,
    email_a: Optional[str],
    login_a: Optional[str],
    name_b: str,
    email_b: Optional[str],
    login_b: Optional[str],
) -> Similarity:
    """
    Compute the best similarity between two identities using all available fields.
    Handles nullable emails/logins gracefully.
    """
    best = check_name_similarity(name_a, name_b)

    # Email vs email comparison
    if email_a and email_b:
        email_sim = check_email_similarity(
            email_address_without_domain(email_a),
            email_address_without_domain(email_b),
        )
        best = most_powerful_similarity(best, email_sim)

    # Login vs login comparison
    if login_a and login_b:
        login_sim = check_login_similarity(login_a, login_b)
        best = most_powerful_similarity(best, login_sim)

    # Cross-field: login vs email prefix (strong signal for cross-source matching)
    if login_a and email_b:
        prefix_b = email_address_without_domain(email_b)
        cross_sim = check_name_similarity(login_a, prefix_b)
        best = most_powerful_similarity(best, cross_sim)

    if login_b and email_a:
        prefix_a = email_address_without_domain(email_a)
        cross_sim = check_name_similarity(login_b, prefix_a)
        best = most_powerful_similarity(best, cross_sim)

    return best


# ── Internal implementation ─────────────────────────────────────────────────────

def _check_similarity(a: str, b: str, thresholds: tuple[int, int, int]) -> Similarity:
    min_prefix, min_suffix, min_token_len = thresholds

    if a == DEFAULT_NOT_SIMILAR or b == DEFAULT_NOT_SIMILAR:
        return Similarity(SimilarityType.DIFFERENT, 0)

    ta = _tokenize(a)
    tb = _tokenize(b)

    if not ta or not tb:
        return Similarity(SimilarityType.DIFFERENT, 0)

    _mark_identical(ta, tb)

    if _all_identical(ta) and _all_identical(tb):
        return Similarity(SimilarityType.IDENTICAL, len(ta))
    if _all_identical(ta):
        return Similarity(SimilarityType.SIMILAR, len(ta))
    if _all_identical(tb):
        return Similarity(SimilarityType.SIMILAR, len(tb))

    _mark_similar_prefix_suffix(ta, tb, min_prefix, min_suffix)

    ta_diff = any(t.kind == SimilarityType.DIFFERENT for t in ta)
    tb_diff = any(t.kind == SimilarityType.DIFFERENT for t in tb)
    ta_max = _max_identical_or_similar_len(ta)
    tb_max = _max_identical_or_similar_len(tb)

    if (ta_diff and tb_max < min_token_len) or (tb_diff and ta_max < min_token_len):
        return Similarity(SimilarityType.DIFFERENT, 0)

    if all(t.kind != SimilarityType.DIFFERENT for t in ta):
        return Similarity(SimilarityType.SIMILAR, len(ta))
    if all(t.kind != SimilarityType.DIFFERENT for t in tb):
        return Similarity(SimilarityType.SIMILAR, len(tb))

    sa = sum(1 for t in ta if t.kind != SimilarityType.DIFFERENT)
    sb = sum(1 for t in tb if t.kind != SimilarityType.DIFFERENT)
    return Similarity(SimilarityType.DIFFERENT, min(sa, sb))


def _tokenize(s: str) -> List[_Token]:
    return [_Token(v) for v in SPLIT_RE.split(s.lower().strip()) if v]


def _mark_identical(a: List[_Token], b: List[_Token]) -> None:
    for x in a:
        for y in b:
            if x.value == y.value:
                x.kind = SimilarityType.IDENTICAL
                y.kind = SimilarityType.IDENTICAL


def _all_identical(tokens: List[_Token]) -> bool:
    return all(t.kind == SimilarityType.IDENTICAL for t in tokens)


def _mark_similar_prefix_suffix(
    a: List[_Token], b: List[_Token], min_prefix: int, min_suffix: int
) -> None:
    changed = True
    while changed:
        changed = False
        for x in a:
            if x.kind != SimilarityType.DIFFERENT:
                continue
            for y in b:
                if y.kind != SimilarityType.DIFFERENT:
                    continue
                p = _common_prefix(x.value, y.value)
                if len(p) == len(x.value) or len(p) == len(y.value) or len(p) >= min_prefix:
                    x.kind = SimilarityType.SIMILAR
                    y.kind = SimilarityType.SIMILAR
                    changed = True
                    break
                s = _common_suffix(x.value, y.value)
                if len(s) == len(x.value) or len(s) == len(y.value) or len(s) >= min_suffix:
                    x.kind = SimilarityType.SIMILAR
                    y.kind = SimilarityType.SIMILAR
                    changed = True
                    break
            if changed:
                break


def _common_prefix(a: str, b: str) -> str:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return a[:i]


def _common_suffix(a: str, b: str) -> str:
    i = 0
    while i < len(a) and i < len(b) and a[-1 - i] == b[-1 - i]:
        i += 1
    return a[len(a) - i:] if i > 0 else ""


def _max_identical_or_similar_len(tokens: List[_Token]) -> int:
    vals = [len(t.value) for t in tokens if t.kind != SimilarityType.DIFFERENT]
    return max(vals) if vals else 0
