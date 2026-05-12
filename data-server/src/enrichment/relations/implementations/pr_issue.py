"""PR ↔ Issue builder.

Subsumes the legacy
``ProjectLinker.link_pull_requests_with_issues`` (in
``src/common/project_linkers.py``) which mutated ``pr.issues`` and
``issue.pull_requests`` list fields — both dropped in v2 per Chunks 4/5.

Two passes (parity with the legacy):

1. **PR-text regex match.** For every PR, scan
   ``(pr.title or "") + " " + (pr.body or "")`` for any known issue
   key. Each unique match contributes a (pr, issue) edge with weight
   1.0.
2. **JIRA-transition mention.** For every Issue, walk
   ``issue.transitions[*].items[*].to_string`` for a
   ``"Pull Request #N"`` substring; parse the PR number, look up the
   matching PR via ``PullRequestRegistry.by_number``, and contribute
   weight 1.0 to the (pr, issue) edge.

Edges that both passes find collapse via the canonical id, so a
PR-text mention + a JIRA-side mention of the same pair produces one
:class:`Relation` with ``strength=2.0`` (legacy behaviour matched
this — the legacy ``_get_or_add`` deduped on object identity, not
weight, but in practice both passes were "yes this is linked, no
strength".  Carrying the count as strength is a v2 enrichment: the
agent now sees "1.0 = single signal, 2.0 = both signals agree").

Lifetime only — PR↔Issue links are static.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable, Optional, Pattern

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph


_PR_NUMBER_RE = re.compile(r"#(\d+)")


@BUILDERS.register
class PrIssueBuilder(RelationBuilder):
    name = "pr.issue"
    relation_kind = "pr_issue"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        issues = _safe_iter(getattr(graph, "issues", None))
        prs = _safe_iter(getattr(graph, "pull_requests", None))
        if not issues or not prs:
            return

        # Build the issue-key regex (case-insensitive) over every known
        # issue. ``issue_by_key`` is the case-folded key → issue map
        # the legacy linker used.
        issue_by_key = {
            issue.key.upper(): issue
            for issue in issues
            if getattr(issue, "key", None)
        }
        issue_pattern = _build_issue_pattern(issue_by_key.keys())

        # Pass 1: PR-text regex match.
        weights: dict[tuple[Any, Any], float] = defaultdict(float)
        if issue_pattern is not None:
            for pr in prs:
                title = getattr(pr, "title", "") or ""
                body = getattr(pr, "body", "") or ""
                text = title + " " + body
                matches = issue_pattern.findall(text)
                if not matches:
                    continue
                pr_ref = pr.ref()
                # ``set`` dedup across the regex matches in a single PR
                # so a key mentioned twice in the body counts once
                # (matches legacy ``for match in set(matches)`` in
                # project_linkers.py:92).
                for key in {m.upper() for m in matches}:
                    issue = issue_by_key.get(key)
                    if issue is None:
                        continue
                    weights[(pr_ref, issue.ref())] += 1.0

        # Pass 2: JIRA-transition mention. The v2 Issue.transitions are
        # value-object lists (per Chunk 5); each transition.items[*]
        # carries a ``to_string`` field with the post-transition value.
        # The legacy linker scanned for ``"Pull Request #N"`` in those
        # post-strings.
        prs_by_number = _by_number_index(graph)
        for issue in issues:
            issue_ref = issue.ref()
            seen_pr_numbers: set[int] = set()
            for transition in getattr(issue, "transitions", None) or []:
                for item in getattr(transition, "items", None) or []:
                    to_string = getattr(item, "to_string", None) or ""
                    if "Pull Request #" not in to_string:
                        continue
                    m = _PR_NUMBER_RE.search(to_string)
                    if m is None:
                        continue
                    pr_number = int(m.group(1))
                    if pr_number in seen_pr_numbers:
                        continue
                    seen_pr_numbers.add(pr_number)
                    matching_prs = prs_by_number(pr_number)
                    for pr in matching_prs:
                        weights[(pr.ref(), issue_ref)] += 1.0

        # Emit one Relation per pair. The pipeline's dedup-by-canonical-id
        # would collapse a same-pair re-emission, but we aggregate
        # weights locally so ``strength`` reflects both passes when
        # they agree.
        for (pr_ref, issue_ref), strength in weights.items():
            rid = Relation.canonical_id(
                pr_ref, issue_ref, "pr_issue", WindowKind.LIFETIME
            )
            yield Relation(
                id=rid,
                source=pr_ref,
                target=issue_ref,
                relation_kind="pr_issue",
                window=WindowKind.LIFETIME,
                strength=float(strength),
            )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _build_issue_pattern(keys: Iterable[str]) -> Optional[Pattern[str]]:
    escaped = [re.escape(k) for k in keys if k]
    if not escaped:
        return None
    # Word-boundary match — same shape as legacy
    # ``project_linkers._build_issue_pattern``.
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


def _by_number_index(graph: Any):
    """Return a callable ``pr_number -> list[PullRequest]``.

    Prefers ``PullRequestRegistry.by_number`` when present (Chunk-5
    ships this index); falls back to a full scan.
    """
    prs = getattr(graph, "pull_requests", None)
    if prs is None:
        return lambda _n: []
    by_number = getattr(prs, "by_number", None)
    if by_number is not None:
        return lambda n: by_number[n]

    def scan(n):
        return [p for p in prs if getattr(p, "number", None) == n]

    return scan


__all__ = ["PrIssueBuilder"]
