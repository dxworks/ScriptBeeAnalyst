"""PR size classifier covers each bucket boundary."""
from __future__ import annotations

from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from src.common.models import GitHubProject, PullRequest

from tests.enrichment.fixtures import EPOCH


def _pr(number: int, changed: int, state: str = "open") -> PullRequest:
    return PullRequest(
        number=number,
        title=f"PR {number}",
        state=state,
        changedFiles=changed,
        body="",
        createdAt=EPOCH,
        mergedAt=None,
        closedAt=None,
        updatedAt=EPOCH,
    )


def _classify(prs: list[PullRequest]) -> dict[int, str]:
    gh = GitHubProject(name="pr-size-synth")
    gh.pull_request_registry.add_all(prs)
    e = compute_enrichments({"git": None, "jira": None, "github": gh}, EnrichmentConfig())
    out: dict[int, str] = {}
    for pr in prs:
        tags = e.tags_by_entity.get(f"pr:{pr.number}")
        assert tags is not None
        out[pr.number] = tags.classifiers["size"]
    return out


def test_pr_size_buckets_cover_S_L_XL():
    cfg = EnrichmentConfig()
    # Defaults: xs<=50, s<=200, m<=600, l<=2000, xl>2000.
    s_pr = _pr(101, cfg.pr_size_xs_max + 1)         # 51 -> S
    l_pr = _pr(102, cfg.pr_size_m_max + 1)          # 601 -> L
    xl_pr = _pr(103, cfg.pr_size_l_max + 1)         # 2001 -> XL
    sizes = _classify([s_pr, l_pr, xl_pr])
    assert sizes[101] == "S"
    assert sizes[102] == "L"
    assert sizes[103] == "XL"


def test_pr_size_xs_at_zero_changed_files():
    sizes = _classify([_pr(200, 0)])
    assert sizes[200] == "XS"
