"""PR ↔ Reviewer.

The current github_models.PullRequest does not carry an explicit `reviewers`
list. Per plan §B-#5 we fall back to `mergedBy` + `assignees` and stamp
`extras={"proxy": True, "source": "mergedBy+assignees"}` on each emitted Relation.
If a future schema grows a `reviewers` attribute, the explicit path is preferred
and proxy=False is set.

Strength = participation count: 1 per appearance (so a reviewer that's also
the merger gets weight 2 if both fields fire).
"""
from __future__ import annotations

from collections import defaultdict

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext


class PullRequestReviewerExtractor:

    KIND = "pr.reviewer"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        github = ctx.graph_data.get("github")
        if github is None:
            return []

        # Lifetime only — reviewer membership doesn't have a temporal axis we
        # can split (no per-review timestamp on the PR model).
        weights: dict[tuple[str, str], float] = defaultdict(float)
        proxy_pairs: set[tuple[str, str]] = set()

        for pr in github.pull_request_registry.all:
            pr_id = str(pr.number)
            explicit = getattr(pr, "reviewers", None) or []
            if explicit:
                for u in explicit:
                    uid = _user_id(u)
                    if uid is None:
                        continue
                    weights[(pr_id, uid)] += 1.0
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
            extras = {}
            if (pr_id, uid) in proxy_pairs:
                extras = {"proxy": True, "source": "mergedBy+assignees"}
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
