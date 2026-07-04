"""
Token-based similarity engine for author name/email comparison.
Ported from the Chronos TokenSimilaritiesAlgorithm.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

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


def _is_single_token(name: str) -> bool:
    """Check if a name has only one token (e.g., just a first name)."""
    tokens = [t for t in SPLIT_RE.split(name.strip()) if t]
    return len(tokens) <= 1


def compute_best_similarity(
    name_a: str,
    email_a: Optional[str],
    login_a: Optional[str],
    name_b: str,
    email_b: Optional[str],
    login_b: Optional[str],
    name_token_df: Optional[Dict[str, int]] = None,
    total_identities: int = 0,
) -> Similarity:
    """
    Compute the best similarity between two identities using all available fields.

    Tracks per-field agreement and requires corroboration from a DIFFERENT
    field before letting weak matches through. A field cannot corroborate
    itself — if the best signal came from name matching, only a non-name
    signal (email, login, cross) counts as corroboration.

    Cross-field comparisons bridge sources that share no directly-comparable
    field (e.g. Git has email-only, GitHub/Jira have login-only):
    - login ↔ email-prefix
    - name  ↔ email-prefix  (catches ``firstname.lastname@`` emails)
    - name  ↔ login         (catches logins that encode the person's name)

    Final guard:
    - SIMILAR → require corroboration from a different field.
    - IDENTICAL with ≤ 2 tokens → require corroboration from a different field,
      UNLESS the matching name is *distinctive* (its shared tokens are rare
      across the whole identity set — see ``name_token_df`` / IDF). This lets
      ``"Dragoș Zaharia"`` merge across sources on the name alone while
      ``"John Smith"`` still demands corroboration.
    - IDENTICAL with ≥ 3 tokens → allowed (truly distinctive names).
    - Single-token name with SIMILAR type → always DIFFERENT (partial
      overlap of single tokens is noise).

    ``name_token_df`` maps a lowercased name token to the number of identities
    it appears in; ``total_identities`` is that set's size. When either is
    absent the distinctiveness relaxation is disabled (behaviour is identical
    to the pre-IDF engine).
    """
    # Source-field tags so we know which field produced the best signal
    FIELD_NAME = "name"
    FIELD_EMAIL = "email"
    FIELD_LOGIN = "login"
    FIELD_CROSS = "cross"

    name_sim = check_name_similarity(name_a, name_b)
    best = name_sim
    best_field = FIELD_NAME

    # Single-token name with SIMILAR is noise — block immediately.
    if best.type == SimilarityType.SIMILAR and (
        _is_single_token(name_a) or _is_single_token(name_b)
    ):
        name_sim = Similarity(SimilarityType.DIFFERENT, 0)
        best = name_sim

    # Track which fields positively agree (SIMILAR or IDENTICAL).
    agreeing_fields: set[str] = set()
    if name_sim.type in (SimilarityType.SIMILAR, SimilarityType.IDENTICAL):
        agreeing_fields.add(FIELD_NAME)

    def _consider(sim: Similarity, field: str) -> None:
        nonlocal best, best_field
        if sim.type in (SimilarityType.SIMILAR, SimilarityType.IDENTICAL):
            agreeing_fields.add(field)
        new_best = most_powerful_similarity(best, sim)
        if new_best is not best:
            best = new_best
            best_field = field

    # Email vs email
    if email_a and email_b:
        email_sim = check_email_similarity(
            email_address_without_domain(email_a),
            email_address_without_domain(email_b),
        )
        _consider(email_sim, FIELD_EMAIL)

    # Login vs login
    if login_a and login_b:
        login_sim = check_login_similarity(login_a, login_b)
        _consider(login_sim, FIELD_LOGIN)

    # Cross-field: login vs email prefix
    if login_a and email_b:
        prefix_b = email_address_without_domain(email_b)
        _consider(check_name_similarity(login_a, prefix_b), FIELD_CROSS)

    if login_b and email_a:
        prefix_a = email_address_without_domain(email_a)
        _consider(check_name_similarity(login_b, prefix_a), FIELD_CROSS)

    # Cross-field: name vs email prefix. Bridges an identity whose email
    # encodes the name (firstname.lastname@) to one that only carries a name.
    if email_b:
        _consider(check_name_similarity(name_a, email_address_without_domain(email_b)), FIELD_CROSS)
    if email_a:
        _consider(check_name_similarity(name_b, email_address_without_domain(email_a)), FIELD_CROSS)

    # Cross-field: name vs login. Bridges a name-only identity to one whose
    # login encodes the name (e.g. "Alice Smith" ↔ login "alicesmith").
    if login_b:
        _consider(check_name_similarity(name_a, login_b), FIELD_CROSS)
    if login_a:
        _consider(check_name_similarity(name_b, login_a), FIELD_CROSS)

    # Corroboration must come from a DIFFERENT field than best_field.
    corroborated = any(f != best_field for f in agreeing_fields)

    if best.type == SimilarityType.SIMILAR and not corroborated:
        return Similarity(SimilarityType.DIFFERENT, 0)
    if (
        best.type == SimilarityType.IDENTICAL
        and best.strength <= 2
        and not corroborated
    ):
        distinctive = best_field == FIELD_NAME and _shared_name_is_distinctive(
            name_a, name_b, name_token_df, total_identities
        )
        if not distinctive:
            return Similarity(SimilarityType.DIFFERENT, 0)

    return best


def _shared_name_is_distinctive(
    name_a: str,
    name_b: str,
    df: Optional[Dict[str, int]],
    total: int,
) -> bool:
    """A 2-token exact-name match may skip corroboration only when every
    shared name token is *rare* across the whole identity set.

    A token is rare if it appears in at most ``max(2, 2% of identities)``
    distinct identities. Common tokens like "john"/"smith" stay above that
    cap and remain corroboration-gated; a surname carried by only the two
    identities being compared (df == 2) always qualifies.
    """
    if not df or total <= 0:
        return False
    shared = {t.value for t in _tokenize(name_a)} & {t.value for t in _tokenize(name_b)}
    if len(shared) < 2:
        return False
    cap = max(2, int(total * 0.02))
    return all(df.get(tok, 0) <= cap for tok in shared)


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
