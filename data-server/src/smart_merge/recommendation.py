"""
Default name/email selection for merge suggestions.
Picks the best display name and email from a group of identities.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from src.common.unified_author import SourceIdentity

GENERIC_EMAIL_DOMAINS = ["github", "users.noreply", "noreply"]


def compute_default_name_email(
    identities: List[SourceIdentity],
    activity_counts: Optional[Dict[str, int]] = None,
) -> Tuple[str, str]:
    """
    Choose the best display name and email from a cluster of identities.

    Args:
        identities: The identities in the suggestion cluster.
        activity_counts: Optional map of identity.key -> activity count (commits, issues, PRs).
                         Used to weight the selection toward more active accounts.
    """
    counts = activity_counts or {}

    # Sort by activity count descending
    ordered = sorted(identities, key=lambda i: counts.get(i.key, 0), reverse=True)

    # Pick best email: first non-generic email from most active identity
    email = _pick_best_email(ordered)

    # Pick best name: richest token set, weighted by activity
    name = _pick_best_name(ordered, counts)

    return name, email


def _pick_best_email(ordered: List[SourceIdentity]) -> str:
    for identity in ordered:
        if identity.email and not _is_generic_email(identity.email):
            return identity.email
    # Fallback to any email
    for identity in ordered:
        if identity.email:
            return identity.email
    return "unknown@unknown"


def _pick_best_name(
    ordered: List[SourceIdentity],
    counts: Dict[str, int],
) -> str:
    candidates: List[Tuple[List[str], int]] = []

    for identity in ordered:
        # Prefer the name field; also consider email prefix and login as name sources
        name_tokens = _normalize_tokens(identity.name)
        best_tokens = name_tokens

        if identity.email:
            email_tokens = _normalize_tokens(_email_local(identity.email))
            if len(email_tokens) > len(best_tokens):
                best_tokens = email_tokens

        if identity.login:
            login_tokens = _normalize_tokens(identity.login)
            if len(login_tokens) > len(best_tokens):
                best_tokens = login_tokens

        if best_tokens:
            candidates.append((best_tokens, counts.get(identity.key, 0)))

    if not candidates:
        return "Unknown"

    # Sort by token richness then activity
    candidates.sort(key=lambda x: (len(x[0]), x[1]), reverse=True)
    best_tokens = candidates[0][0]
    return " ".join(_capitalize(t) for t in best_tokens)


def _normalize_tokens(s: str) -> List[str]:
    parts = re.split(r"\s|\.|-", s.lower())
    out = []
    for p in parts:
        p2 = re.sub(r"[0-9]", "", p).strip()
        if p2:
            out.append(p2)
    return out


def _email_local(email: str) -> str:
    return email.rsplit("@", 1)[0] if "@" in email else email


def _is_generic_email(email: str) -> bool:
    lower = email.lower()
    domain = lower.rsplit("@", 1)[-1]
    return any(lower.startswith(d) or domain.startswith(d) for d in GENERIC_EMAIL_DOMAINS)


def _capitalize(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s
