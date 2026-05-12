"""PR ↔ Reviewer builder.

Port of legacy ``src/enrichment/relations/pr_reviewer.py``. Two paths:

* **Preferred** — :class:`Review` rows in ``state ∈ {APPROVED,
  CHANGES_REQUESTED}`` with a typed ``author_ref``. Stamps
  ``extras={"proxy": False, "source": "Review.author"}``.
* **Fallback (proxy)** — when no qualifying review exists on a PR, fall
  back to the PR's ``merged_by_ref`` + ``assignee_refs``. Stamps
  ``extras={"proxy": True, "source": "mergedBy+assignees"}``.

Lifetime only — reviewer membership has no temporal axis.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations_v2 import Relation, RelationBuilder, WindowKind
from src.enrichment.relations_v2.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import Graph

_RELATION_QUALIFYING_STATES = {"APPROVED", "CHANGES_REQUESTED"}


@BUILDERS.register
class PrReviewerBuilder(RelationBuilder):
    name = "pr.reviewer"
    relation_kind = "pr_reviewer"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        prs = _safe_iter(getattr(graph, "pull_requests", None))
        if not prs:
            return

        reviews_get = _entity_by_id(getattr(graph, "reviews", None))

        weights: dict[tuple[Any, Any], float] = defaultdict(float)
        explicit_pairs: set[tuple[Any, Any]] = set()
        proxy_pairs: set[tuple[Any, Any]] = set()

        for pr in prs:
            pr_ref = pr.ref()
            review_refs = getattr(pr, "review_refs", None) or []

            explicit_emitted = False
            for review_ref in review_refs:
                review = reviews_get(review_ref.id)
                if review is None:
                    continue
                state = (getattr(review, "state", None) or "").upper()
                if state not in _RELATION_QUALIFYING_STATES:
                    continue
                author_ref = getattr(review, "author_ref", None)
                if author_ref is None:
                    continue
                weights[(pr_ref, author_ref)] += 1.0
                explicit_pairs.add((pr_ref, author_ref))
                explicit_emitted = True

            if explicit_emitted:
                continue

            merged_by_ref = getattr(pr, "merged_by_ref", None)
            if merged_by_ref is not None:
                weights[(pr_ref, merged_by_ref)] += 1.0
                proxy_pairs.add((pr_ref, merged_by_ref))

            for assignee_ref in getattr(pr, "assignee_refs", None) or []:
                weights[(pr_ref, assignee_ref)] += 1.0
                proxy_pairs.add((pr_ref, assignee_ref))

        for (pr_ref, author_ref), strength in weights.items():
            if (pr_ref, author_ref) in explicit_pairs:
                extras = {"proxy": False, "source": "Review.author"}
            elif (pr_ref, author_ref) in proxy_pairs:
                extras = {"proxy": True, "source": "mergedBy+assignees"}
            else:
                extras = {}
            rid = Relation.canonical_id(
                pr_ref, author_ref, "pr_reviewer", WindowKind.LIFETIME
            )
            yield Relation(
                id=rid,
                source=pr_ref,
                target=author_ref,
                relation_kind="pr_reviewer",
                window=WindowKind.LIFETIME,
                strength=float(strength),
                extras=extras,
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


def _entity_by_id(reg: Any):
    if reg is None:
        return lambda _id: None
    get = getattr(reg, "get", None)
    if get is None:
        return lambda _id: None
    return get


__all__ = ["PrReviewerBuilder"]
