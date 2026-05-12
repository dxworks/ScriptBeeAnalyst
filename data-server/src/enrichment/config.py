"""Thresholds and regexes for the enrichment layer.

Mirrors dx's `Initializer` class — a single tunable surface. Values chosen to
match dx defaults where applicable; callers may override by passing a custom
EnrichmentConfig to ``run_pipeline``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Pattern


# ── Regexes (commit message classification) ─────────────────────────────────────

# Order matters: first match wins.
NATURE_PATTERNS: list[tuple[str, Pattern[str]]] = [
    ("merge",    re.compile(r"^\s*Merge\b", re.IGNORECASE)),
    ("revert",   re.compile(r"\brevert(s|ed|ing)?\b|^Revert\b", re.IGNORECASE)),
    ("bugfix",   re.compile(r"\b(fix(es|ed|ing)?|bug(fix)?|hotfix|patch|issue|resolve[sd]?|close[sd]?)\b", re.IGNORECASE)),
    ("docs",     re.compile(r"\b(docs?|documentation|readme|javadoc)\b", re.IGNORECASE)),
    ("test",     re.compile(r"\b(test(s|ing)?|spec|junit|pytest|qa)\b", re.IGNORECASE)),
    ("refactor", re.compile(r"\b(refactor(s|ed|ing)?|clean(up)?|rename|restructur|simplif|format|lint)\b", re.IGNORECASE)),
    ("chore",    re.compile(r"\b(chore|bump|upgrade|dependenc|version|release|build|ci|config)\b", re.IGNORECASE)),
    ("feature",  re.compile(r"\b(add(s|ed|ing)?|feat(ure)?|implement(s|ed|ing)?|introduc(e|es|ed|ing)|new)\b", re.IGNORECASE)),
]

# File role heuristics by path segment.
TEST_PATTERNS = [
    re.compile(r"(^|/)(tests?|__tests__|spec|specs|testing)(/|$)", re.IGNORECASE),
    re.compile(r"\.(test|spec)\.[a-z]+$", re.IGNORECASE),
    re.compile(r"_test\.[a-z]+$", re.IGNORECASE),
    re.compile(r"Test[A-Z][A-Za-z0-9]+\.java$"),
]

DOC_PATTERNS = [
    re.compile(r"(^|/)(docs?|documentation|readme)(/|$)", re.IGNORECASE),
    re.compile(r"\.(md|rst|adoc|txt)$", re.IGNORECASE),
]

CONFIG_PATTERNS = [
    re.compile(r"\.(ya?ml|toml|ini|cfg|conf|properties|json|env)$", re.IGNORECASE),
    re.compile(r"(^|/)(config|conf|settings)(/|$)", re.IGNORECASE),
    re.compile(r"(^|/)\.[a-z]+(rc|ignore|config)$", re.IGNORECASE),
]

BUILD_PATTERNS = [
    re.compile(r"(^|/)(pom\.xml|build\.gradle(\.kts)?|package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.toml|Cargo\.lock|go\.mod|go\.sum|requirements[^/]*\.txt|Pipfile(\.lock)?|pyproject\.toml|setup\.(py|cfg)|Makefile|CMakeLists\.txt|Dockerfile|docker-compose\.ya?ml)$", re.IGNORECASE),
    re.compile(r"(^|/)(\.mvn|\.gradle|gradle|\.github|\.gitlab-ci\.yml|Jenkinsfile)(/|$)"),
]


@dataclass
class EnrichmentConfig:
    """All knobs in one place. Adjust then re-run ``run_pipeline``."""

    # Time windows
    recent_window_days: int = 90
    idle_threshold_days: int = 180

    # Commit churn buckets (sum added+deleted over all hunks)
    churn_focused_max: int = 50
    churn_medium_max: int = 500

    # Commit spread buckets (distinct files touched)
    spread_narrow_max: int = 3

    # Cap on files per commit when extracting co-change pairs.
    # Mirrors dx's `Commit.hasModerateNumberOfChanges()` — bulk commits over
    # this many files (renames, format passes) drown out real coupling signal.
    cochange_max_files_per_commit: int = 20

    # Daytime buckets (local hour 0..23)
    daytime_buckets: dict[str, tuple[int, int]] = field(default_factory=lambda: {
        "night":   (0, 6),
        "morning": (6, 12),
        "afternoon": (12, 18),
        "evening": (18, 24),
    })

    # Author seniority (days between first and last commit)
    newcomer_max_days: int = 30
    established_max_days: int = 180
    senior_max_days: int = 730

    # Anomaly thresholds (phase 2)
    bugmagnet_min_bugfix_commits: int = 5      # min absolute count to even consider
    bugmagnet_ratio_min: float = 0.40          # share of commits-on-file that are bugfixes
    orphan_min_commits: int = 1                # at least one change exists
    hermit_dominance_ratio: float = 0.80       # >80% churn by one author -> BusFactor1
    busfactor1_min_distinct_authors: int = 2   # need >=2 authors so dominance is meaningful
    shared_knowledge_entropy_min: float = 1.5  # nats; ~ >=4 evenly-active authors
    shared_knowledge_min_distinct_authors: int = 3  # entropy is noisy below this
    bazaar_distinct_authors_min: int = 5       # distinct recent-window authors
    cathedral_dominance_ratio: float = 0.80    # one author owns recent window
    cathedral_min_recent_commits: int = 4      # avoid flagging files with 1 recent touch
    pulsar_cv_min: float = 1.0                 # CV of inter-commit intervals (lifetime)
    pulsar_min_commits: int = 6                # CV is noise below this
    pulsar_min_intervals: int = 3              # need >=3 inter-commit gaps so variance is defined
    pivotfile_cochange_degree_min: int = 10    # number of co-change neighbours
    tasksbottleneck_open_age_days: int = 180   # open longer than this -> bottleneck
    tasksbottleneck_min_in_flight: int = 10    # author with >=N open issues -> bottleneck

    # PR size thresholds — buckets over (changedFiles + total linked-commit churn).
    # Defaults align with industry "small PR" guidance (<200 lines).
    pr_size_xs_max: int = 50
    pr_size_s_max: int = 200
    pr_size_m_max: int = 600
    pr_size_l_max: int = 2000

    # ── A2.5 PR review metrics ─────────────────────────────────────────────────
    # `pr.review_intensity` count buckets: 0=none, 1..light_max=light,
    # light_max+1..heavy_min-1=moderate, >=heavy_min=heavy.
    review_intensity_light_max: int = 2
    review_intensity_heavy_min: int = 5
    # `anomaly.review.StalledReview`: open PRs older than this with no recent
    # review activity (no reviews at all OR last review older than threshold).
    stalled_review_open_days_min: int = 14

    # Phase 3 proxy traits — flagged as proxy in evidence per plan §B-#5.
    # Net-churn floor for Supernova; chosen at 5k so only files with sustained
    # heavy contribution flag (median project files churn <500 over their life).
    supernova_net_churn_min: int = 5000

    # ── B2 — JaFax / CodeFrame thresholds ──────────────────────────────────
    # ZoneCrossroad: minimum commits a file must have inside one UTC offset
    # before that offset counts as "significant". dx default
    # (ZoneCrossroad.java:16) is 10.
    zonecrossroad_min_zone_commits: int = 10
    # ConcurrentZoneCrossroad: number of (year, month) periods with >= 2
    # active zones above which severity tiers up. dx default ~5.
    concurrent_zonecrossroad_strict_threshold: int = 5
    # FeatureEncapsulationOverview thresholds (commit-spread / churn buckets).
    # Wide commit = touches >= this many files; deep commit = churn over this many lines.
    feature_encapsulation_wide_files_min: int = 20
    feature_encapsulation_deep_churn_min: int = 500
    # High-impact task = touches >= this many files; scattered task = touches
    # >= this many components.
    feature_encapsulation_high_impact_files_min: int = 10
    feature_encapsulation_scattered_components_min: int = 3

    # ── B1 — Lizard thresholds ─────────────────────────────────────────────
    # DynamicBlob = high-LOC + high-churn. dx defaults: hugefile_threshold=500
    # (DynamicBlob.java line 29) and frequentchanges_threshold=20
    # (Initializer.java line 73). Severity follows DynamicBlob.java lines 27-38.
    dynamicblob_loc_min: int = 500
    dynamicblob_changes_min: int = 20
    # Max number of commits on a production file that ALSO touch a test-role
    # file before TestOrphan stops firing. 1 leaves room for ad-hoc smoke tests.
    test_orphan_max_cochange_test_count: int = 1
    # Min commits a production file needs before TestOrphan even applies (avoid
    # flagging brand-new untested-yet code as orphaned).
    test_orphan_min_commits: int = 3

    # Phase 3 components — optional mapping JSON path; missing file = heuristic.
    components_mapping_path: Optional[str] = None

    # ── A2.1 file-level traits ─────────────────────────────────────────────────
    # Knowledge family
    accumulator_bucket_weeks: int = 4
    accumulator_min_windows: int = 6
    owner_churn_dominance_threshold: float = 0.5
    polarised_top_share: float = 0.8
    polarised_min_authors: int = 2
    solitaire_min_lifetime_commits: int = 5
    team_churn_set_change_ratio: float = 0.5
    weak_owner_max_share: float = 0.2
    weak_owner_min_active_authors: int = 2

    # ── A2.2 author-level traits ───────────────────────────────────────────────
    orphancauser_min_orphan_files: int = 3
    orphancauser_min_lifetime_commits: int = 10
    orphancauser_orphan_sample_cap: int = 20

    # Cohesion family
    hibernator_min_lifetime_commits: int = 5
    awakening_min_dormant_weeks: int = 12
    awakening_recent_commits_min: int = 1
    erosion_window_weeks: int = 4
    erosion_trend_max: float = -0.5
    flicker_cv_min: float = 1.2
    flicker_min_recent_commits: int = 4
    frequent_changer_lifetime_min: int = 50
    frequent_changer_recent_min: int = 10

    # Testing family
    refactoring_magnet_min_commits: int = 10

    # Structuring family
    identical_filenames_min_count: int = 2
    identical_filenames_peer_cap: int = 20

    # ── A2.3 relations ─────────────────────────────────────────────────────────
    # Δt window for time-windowed cochange (file-file and author-author).
    time_windowed_cochange_hours: int = 24
    # similarity.file-file.names
    name_similarity_min_score: float = 0.85
    # If True, only compare files sharing the same extension (cuts O(N²) cost).
    name_similarity_extension_filter: bool = False
    # Per-file cap so a single file can't dominate the edge list.
    name_similarity_max_pairs_per_file: int = 50

    # Issue age buckets — boundaries in days; first match wins.
    issue_age_buckets: list[tuple[str, int]] = field(default_factory=lambda: [
        ("<1w", 7),
        ("1-4w", 28),
        ("1-3m", 90),
        ("3-12m", 365),
        (">1y", 10**9),
    ])

    # Issue resolution: status-category names (case-insensitive) treated as "done".
    resolved_status_categories: tuple[str, ...] = (
        "done", "closed", "resolved", "complete", "completed",
    )

    # Regexes — exposed so callers can extend without patching the module.
    nature_patterns: list[tuple[str, Pattern[str]]] = field(default_factory=lambda: list(NATURE_PATTERNS))
    test_patterns: list[Pattern[str]] = field(default_factory=lambda: list(TEST_PATTERNS))
    doc_patterns: list[Pattern[str]] = field(default_factory=lambda: list(DOC_PATTERNS))
    config_patterns: list[Pattern[str]] = field(default_factory=lambda: list(CONFIG_PATTERNS))
    build_patterns: list[Pattern[str]] = field(default_factory=lambda: list(BUILD_PATTERNS))


DEFAULT_CONFIG = EnrichmentConfig()
