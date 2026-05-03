"""File↔File path similarity (basename string-similarity).

Lifetime only — file paths have no recent semantic. Strength is the
`difflib.SequenceMatcher` ratio over file *basenames* (a/b/foo.py → foo.py).

Bounding the O(N²) cost:
  * `name_similarity_extension_filter=True` restricts comparisons to same-ext
    pairs (cheap pre-filter).
  * `name_similarity_max_pairs_per_file` keeps only the top-N ratios per
    source file. Implemented with a simple per-file sorted list cap.
"""
from __future__ import annotations

import os
from difflib import SequenceMatcher

from src.enrichment.models import Relation, RelationFile
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


class FileNameSimilarityExtractor:

    KIND = "similarity.file-file.names"

    def extract(self, ctx: TaggingContext) -> list[RelationFile]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cfg = ctx.config
        min_score = cfg.name_similarity_min_score
        ext_filter = cfg.name_similarity_extension_filter
        cap = cfg.name_similarity_max_pairs_per_file

        ids: list[str] = []
        for f in git.file_registry.all:
            fid = _file_id(f)
            if fid:
                ids.append(fid)
        ids = sorted(set(ids))

        # Bucket by extension if filtering, else single bucket.
        buckets: dict[str, list[str]] = {}
        for fid in ids:
            base = os.path.basename(fid)
            ext = os.path.splitext(base)[1].lower() if ext_filter else ""
            buckets.setdefault(ext, []).append(fid)

        # per-file priority cap: keep top-cap edges per fid (sorted desc by score).
        per_file_top: dict[str, list[tuple[float, str]]] = {}

        def _consider(fid: str, other: str, score: float) -> None:
            arr = per_file_top.setdefault(fid, [])
            arr.append((score, other))
            if len(arr) > cap:
                arr.sort(key=lambda kv: -kv[0])
                del arr[cap:]

        for bucket in buckets.values():
            n = len(bucket)
            for i in range(n):
                a = bucket[i]
                base_a = os.path.basename(a)
                for j in range(i + 1, n):
                    b = bucket[j]
                    base_b = os.path.basename(b)
                    score = SequenceMatcher(None, base_a, base_b).ratio()
                    if score < min_score:
                        continue
                    _consider(a, b, score)
                    _consider(b, a, score)

        # Final pass: an edge (a,b) survives only if BOTH endpoints kept it
        # (so the cap is symmetric and we don't emit half-edges).
        kept: dict[tuple[str, str], float] = {}
        for fid, arr in per_file_top.items():
            for score, other in arr:
                pair = tuple(sorted((fid, other)))
                # only insert once we see the pair from both sides
                if pair in kept:
                    continue
                # check symmetric
                other_arr = per_file_top.get(other, [])
                if any(o == fid for _, o in other_arr):
                    kept[pair] = score

        rels = [
            Relation(
                source_kind="file",
                source_id=a,
                target_kind="file",
                target_id=b,
                kind=self.KIND,
                strength=round(float(score), 4),
            )
            for (a, b), score in sorted(kept.items(), key=lambda kv: -kv[1])
        ]
        return [RelationFile(kind=self.KIND, window="lifetime", relations=rels)]
