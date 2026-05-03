"""PR ↔ Reviewer.

Preferred path: explicit `Review.user` entries on `pr.reviews`. Per A1 the
GitHub miner now hydrates the `reviews` array on PullRequest, so for any PR
that has at least one APPROVED or CHANGES_REQUESTED review we emit edges
straight from `Review.user` and stamp `extras={"proxy": False, "source":
"Review.user"}`.

Fallback path (no qualifying reviews on the PR): we keep the original Phase-3
proxy — `mergedBy` + `assignees` — and stamp `extras={"proxy": True, "source":
"mergedBy+assignees"}`. The fallback is only consulted when the explicit-review
path produced no edges for that PR, so a PR with one APPROVED review never
re-fires the proxy fallback.

COMMENTED and PENDING reviews are intentionally excluded from this relation
(they don't constitute a review verdict). They DO still count toward
`pr.review_intensity` — see `issue_pr_classifiers.py`.

Strength = participation count (one per qualifying review/appearance), so a
reviewer who APPROVED twice gets weight 2 on that PR.
"""
from __future__ import annotations

from collections import defaultdict

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext


_RELATION_QUALIFYING_STATES = {"APPROVED", "CHANGES_REQUESTED"}


class PullRequestReviewerExtractor:

    KIND = "pr.reviewer"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        github = ctx.graph_data.get("github")
        if github is None:
            return []

        # Lifetime only — reviewer membership has no temporal axis we can split
        # cleanly per-PR (we'd need per-edge submittedAt and a recent cutoff).
        weights: dict[tuple[str, str], float] = defaultdict(float)
        # Track which path produced each edge so the fallback never overrides
        # explicit-review edges.
        proxy_pairs: set[tuple[str, str]] = set()
        explicit_pairs: set[tuple[str, str]] = set()

        for pr in github.pull_request_registry.all:
            pr_id = str(pr.number)

            explicit_emitted_for_pr = False
            for review in getattr(pr, "reviews", None) or []:
                state = (getattr(review, "state", None) or "").upper()
                if state not in _RELATION_QUALIFYING_STATES:
                    continue
                uid = _user_id(getattr(review, "user", None))
                if uid is None:
                    continue
                weights[(pr_id, uid)] += 1.0
                explicit_pairs.add((pr_id, uid))
                explicit_emitted_for_pr = True

            if explicit_emitted_for_pr:
                continue

            merged_by = getattr(pr, "mergedBy", None)
            mb_id = _user_id(merged_by) if merged_by is not None else None
            if mb_id is not None:
                weights[(pr_id, mb_id)] += 1.0
                proxy_pairs.add((pr_id, mb_id))

            for u in getattr(pr, "assignees", None) or []:
                uid = _user_id(u)
                if uid is None:
                    continue
                weights[(pr_id, uid)] += 1.0
                proxy_pairs.add((pr_id, uid))

        relations = []
        for (pr_id, uid), strength in sorted(weights.items(), key=lambda kv: -kv[1]):
            if (pr_id, uid) in explicit_pairs:
                extras = {"proxy": False, "source": "Review.user"}
            elif (pr_id, uid) in proxy_pairs:
                extras = {"proxy": True, "source": "mergedBy+assignees"}
            else:
                extras = {}
            relations.append(Relation(
                source_kind="pr",
                source_id=pr_id,
                target_kind="author",
                target_id=uid,
                kind=self.KIND,
                strength=float(strength),
                extras=extras,
            ))

        return [RelationFile(kind=self.KIND, window="lifetime", relations=relations)]


def _user_id(user) -> str | None:
    # `login` is the canonical GitHub identifier; `name` and `url` are
    # best-effort fallbacks for users where the miner output didn't capture login.
    if user is None:
        return None
    for attr in ("login", "name", "url"):
        v = getattr(user, attr, None)
        if v:
            return str(v)
    return None
