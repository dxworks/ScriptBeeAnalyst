"""Task-prefix extraction (Phase 2 decision D3).

Commits frequently include a Jira-style task key in the message
(e.g. ``PROJ-123: fix the bug``). The prefix is the ``PROJ`` portion —
the project key — which downstream relation builders use as a "shared
work item" signal (``cochange_*_shared_task_prefixes``).

The :data:`TASK_PREFIX_PATTERN` regex matches a Jira-style key at the
start of the message; :func:`parse_task_prefix` returns the
``(prefix, remainder)`` tuple for the first match; :func:`extract_task_prefixes`
scans the whole message and returns the deduplicated, ordered list of
prefixes (a single commit may mention several keys — e.g.
``PROJ-1, PROJ-2 and OTHER-3``).

Per D3 the parser only handles the prefix component (letters before the
hyphen); the numeric id portion is recognised by the regex but discarded
here because the classifier dimension semantics call for the project
key only.
"""
from __future__ import annotations

import re
from typing import Final, Optional, Tuple

# ``^[A-Z][A-Z0-9_]+-\d+`` matches at the start of a string. We use the
# *un-anchored* variant for :func:`extract_task_prefixes` (finditer over
# the whole message) and re-anchor inline for :func:`parse_task_prefix`
# so the public ``TASK_PREFIX_PATTERN`` stays composable.
#
# Threshold notes:
#   * Minimum prefix length = 2 chars (one leading letter + at least one
#     letter/digit/underscore — keeps trivial "A-1" out of the catalog).
#   * Numeric id required (matches Jira's enforced ``KEY-NUMBER`` shape;
#     "ABC-something" never registers as a task reference).
#
# Configurable via :data:`MIN_PREFIX_LEN` — downstream callers can vary
# the bar without rewriting the regex.
MIN_PREFIX_LEN: Final[int] = 2

TASK_PREFIX_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b([A-Z][A-Z0-9_]+)-(\d+)\b"
)


def parse_task_prefix(message: str) -> Optional[Tuple[str, str]]:
    """Return ``(prefix, remainder)`` for the first task key at the start
    of ``message``, or ``None`` if none matches.

    Anchored at the start of the message (after leading whitespace) — for
    the "leading task key" convention. Use :func:`extract_task_prefixes`
    to scan the whole message body.

    >>> parse_task_prefix("PROJ-12: fix bug")
    ('PROJ', ': fix bug')
    >>> parse_task_prefix("merge PROJ-12 into main")  # not at start
    >>> parse_task_prefix("")
    """
    if not message:
        return None
    stripped = message.lstrip()
    leading_ws = len(message) - len(stripped)
    match = re.match(r"([A-Z][A-Z0-9_]+)-(\d+)\b", stripped)
    if match is None:
        return None
    prefix = match.group(1)
    if len(prefix) < MIN_PREFIX_LEN:
        return None
    remainder = message[leading_ws + match.end():]
    return prefix, remainder


def extract_task_prefixes(message: str) -> list[str]:
    """Return every distinct task prefix mentioned in ``message`` in order
    of first appearance.

    Used by :class:`CommitTaskPrefixClassifierMetric` so a single commit
    that mentions several keys emits one classifier per distinct prefix.

    >>> extract_task_prefixes("PROJ-1, PROJ-2 and OTHER-3")
    ['PROJ', 'OTHER']
    >>> extract_task_prefixes("no key here")
    []
    """
    if not message:
        return []
    seen: list[str] = []
    seen_set: set[str] = set()
    for match in TASK_PREFIX_PATTERN.finditer(message):
        prefix = match.group(1)
        if len(prefix) < MIN_PREFIX_LEN:
            continue
        if prefix in seen_set:
            continue
        seen_set.add(prefix)
        seen.append(prefix)
    return seen


__all__ = [
    "MIN_PREFIX_LEN",
    "TASK_PREFIX_PATTERN",
    "extract_task_prefixes",
    "parse_task_prefix",
]
