"""Graph root for the v2 model — typed registry fields edition.

Chunk 8 of the architectural refactor. Per §1.6 of
``architectural_changes.md``, the dict-of-registries placeholder from
Chunk 1 is replaced with named typed fields, one per concrete
:class:`Registry` subclass shipped by Chunks 2/4/5/6/7. The accessors
(:meth:`registry_for`, :meth:`resolve`, :meth:`lazy`, :meth:`dump`)
keep the same signatures so existing call-sites compile unchanged.

Backwards-compat: the legacy ``registries=`` constructor kwarg (a dict
keyed by :class:`EntityKind`) is still accepted — a ``model_validator``
fans it out into the matching typed fields at construction time. The
``g.registries`` read-only property returns the same dict shape so
read-only consumers don't have to switch. Both seams are documented
below and exist only to keep Chunk-1/2/3 tests green during this
transition; new code should use the typed fields directly.

Field names are stable: chunks 4–7 use these exact attribute names on
``host`` (the :class:`PipelineHost` protocol). Renaming any one of
``commits``/``files``/``changes``/.../``relations`` would break the
pipeline silently.

``BuilderRegistry`` / ``MetricRegistry`` / ``OverviewTableRegistry`` are
module-level catalogs of code (not entity registries) and intentionally
do NOT live on :class:`Graph` — they're singleton catalogs imported
from their packages, see ``architectural_changes.md`` §7 + Chunk-3 / 7
design choices.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    Optional,
    Tuple,
)

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .entity import Entity
from .kinds import EntityKind
from .ref import EntityRef
from .registry import Registry

# --- typed registry imports ---------------------------------------------------
# Each registry import is local-package-qualified to avoid star-importing.
# Importing every registry up-front is acceptable here: this is the root of
# the v2 model, and downstream code always imports ``Graph`` via the
# ``src.common.kernel`` re-export — the import overhead is one-shot.
#
# People
from ..people.unified import UnifiedUserRegistry

# Domain registries
from ..domains.code_structure.registries import (
    CodeFieldRegistry,
    CodeMethodRegistry,
    CodeReferenceRegistry,
    CodeStructureProjectRegistry,
    CodeTypeRegistry,
)
from ..domains.components.registries import ComponentRegistry
from ..domains.duplication.registries import (
    DuplicationPairRegistry,
    DuplicationProjectRegistry,
)
from ..domains.git.registries import (
    ChangeRegistry,
    CommitRegistry,
    FileRegistry,
    GitAccountRegistry,
    GitProjectRegistry,
    HunkRegistry,
)
from ..domains.github.registries import (
    GitHubCommitRegistry,
    GitHubProjectRegistry,
    GitHubUserRegistry,
    PullRequestRegistry,
    ReviewCommentRegistry,
    ReviewRegistry,
)
from ..domains.jira.registries import (
    IssueRegistry,
    IssueStatusRegistry,
    IssueTypeRegistry,
    JiraProjectRegistry,
    JiraUserRegistry,
)
from ..domains.metrics_lizard.registries import (
    FileMetricRegistry,
    LizardMetricsProjectRegistry,
)
from ..domains.quality.registries import (
    QualityIssueRegistry,
    QualityProjectRegistry,
)

# Enrichment registries
from ...enrichment.relations.registries import RelationRegistry
from ...enrichment.tags.registries import ClassifierRegistry, TraitRegistry

if TYPE_CHECKING:
    from ..pickle_store import PickleStore
    from ...enrichment.utils.temporal import TemporalIndex


#: Current schema version written by ``Graph.dump`` and required by
#: ``Graph.lazy``. Bumping = re-import (greenfield migration, no readers
#: kept for old majors — see §8.3 of the plan).
SCHEMA_VERSION: int = 2


# ---------------------------------------------------------------------------
# Field-name <-> registry-class catalog.
# ---------------------------------------------------------------------------
# A single source of truth for:
#   (a) which fields exist + their concrete registry classes
#   (b) which EntityKind each field dispatches to (for ``registry_for``
#       and ``resolve``)
#   (c) the on-disk registry name used by ``dump`` / ``lazy``
#
# Multiple project-domain registries map to ``EntityKind.PROJECT``;
# ``registry_for(PROJECT)`` therefore can't return a single registry. It
# returns ``None`` for PROJECT, and :meth:`resolve` walks every project
# registry instead. The :meth:`project_registries` accessor exposes the
# list for callers that need to dispatch by ``isinstance``.
#
# Each entry: (field_name, registry_class, entity_kind, on_disk_name).
_FieldSpec = Tuple[str, type, EntityKind, str]


_FIELD_SPECS: tuple[_FieldSpec, ...] = (
    # --- people -----------------------------------------------------------
    ("unified_users", UnifiedUserRegistry, EntityKind.UNIFIED_USER, "unified_user"),
    ("git_accounts",  GitAccountRegistry,  EntityKind.GIT_ACCOUNT,  "git_account"),
    ("jira_users",    JiraUserRegistry,    EntityKind.JIRA_USER,    "jira_user"),
    ("github_users",  GitHubUserRegistry,  EntityKind.GITHUB_USER,  "github_user"),

    # --- projects (all share EntityKind.PROJECT) ---------------------------
    ("git_projects",            GitProjectRegistry,            EntityKind.PROJECT, "git_project"),
    ("jira_projects",           JiraProjectRegistry,           EntityKind.PROJECT, "jira_project"),
    ("github_projects",         GitHubProjectRegistry,         EntityKind.PROJECT, "github_project"),
    ("code_structure_projects", CodeStructureProjectRegistry,  EntityKind.PROJECT, "code_structure_project"),
    ("duplication_projects",    DuplicationProjectRegistry,    EntityKind.PROJECT, "duplication_project"),
    ("quality_projects",        QualityProjectRegistry,        EntityKind.PROJECT, "quality_project"),
    ("lizard_projects",         LizardMetricsProjectRegistry,  EntityKind.PROJECT, "lizard_project"),

    # --- git --------------------------------------------------------------
    ("commits", CommitRegistry, EntityKind.COMMIT, "commit"),
    ("files",   FileRegistry,   EntityKind.FILE,   "file"),
    ("changes", ChangeRegistry, EntityKind.CHANGE, "change"),
    ("hunks",   HunkRegistry,   EntityKind.HUNK,   "hunk"),

    # --- jira -------------------------------------------------------------
    ("issues",         IssueRegistry,       EntityKind.ISSUE,        "issue"),
    ("issue_statuses", IssueStatusRegistry, EntityKind.ISSUE_STATUS, "issue_status"),
    ("issue_types",    IssueTypeRegistry,   EntityKind.ISSUE_TYPE,   "issue_type"),

    # --- github -----------------------------------------------------------
    ("pull_requests",   PullRequestRegistry,   EntityKind.PULL_REQUEST,   "pull_request"),
    ("reviews",         ReviewRegistry,        EntityKind.REVIEW,         "review"),
    ("review_comments", ReviewCommentRegistry, EntityKind.REVIEW_COMMENT, "review_comment"),
    ("github_commits",  GitHubCommitRegistry,  EntityKind.GITHUB_COMMIT,  "github_commit"),

    # --- code structure ---------------------------------------------------
    ("code_types",   CodeTypeRegistry,      EntityKind.CODE_TYPE,   "code_type"),
    ("code_methods", CodeMethodRegistry,    EntityKind.CODE_METHOD, "code_method"),
    ("code_fields",  CodeFieldRegistry,     EntityKind.CODE_FIELD,  "code_field"),
    ("code_refs",    CodeReferenceRegistry, EntityKind.CODE_REF,    "code_ref"),

    # --- duplication / quality / lizard ----------------------------------
    ("duplications",   DuplicationPairRegistry, EntityKind.DUPLICATION_PAIR, "duplication_pair"),
    ("quality_issues", QualityIssueRegistry,    EntityKind.QUALITY_ISSUE,    "quality_issue"),
    ("file_metrics",   FileMetricRegistry,      EntityKind.FILE_METRIC,      "file_metric"),

    # --- components + enrichment -----------------------------------------
    ("components",  ComponentRegistry,  EntityKind.COMPONENT,  "component"),
    ("traits",      TraitRegistry,      EntityKind.TRAIT,      "trait"),
    ("classifiers", ClassifierRegistry, EntityKind.CLASSIFIER, "classifier"),
    ("relations",   RelationRegistry,   EntityKind.RELATION,   "relation"),
)

#: Field name → concrete registry class.
_FIELD_TO_CLS: Dict[str, type] = {name: cls for name, cls, _, _ in _FIELD_SPECS}

#: Field name → on-disk registry name (used by dump/lazy).
_FIELD_TO_DISK: Dict[str, str] = {name: disk for name, _, _, disk in _FIELD_SPECS}

#: Field name → EntityKind owned by that field.
_FIELD_TO_KIND: Dict[str, EntityKind] = {name: kind for name, _, kind, _ in _FIELD_SPECS}

#: EntityKind → list of field names that own that kind. Most kinds map to
#: exactly one field; ``EntityKind.PROJECT`` maps to all 7 project
#: registries because v2 keeps a per-domain Project registry (Chunk 2
#: design choice §5 deferred the "single ProjectRegistry" decision to
#: Chunk 8 — we decide here to keep them separate).
_KIND_TO_FIELDS: Dict[EntityKind, list[str]] = {}
for _name, _cls, _kind, _disk in _FIELD_SPECS:
    _KIND_TO_FIELDS.setdefault(_kind, []).append(_name)


# ---------------------------------------------------------------------------
# The Graph
# ---------------------------------------------------------------------------
class Graph(BaseModel):
    """Root of the v2 graph.

    Every Entity registry shipped by the domain chunks lives here as a
    typed field with a sane empty default. The accessors keep the
    legacy dict-of-registries shape working for tests written against
    Chunk 1 / Chunk 2; new call-sites read the typed fields directly.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    # --- meta ---
    schema_version: int = SCHEMA_VERSION
    project_id: str
    built_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- people ---
    unified_users: UnifiedUserRegistry = Field(default_factory=UnifiedUserRegistry)
    git_accounts:  GitAccountRegistry  = Field(default_factory=GitAccountRegistry)
    jira_users:    JiraUserRegistry    = Field(default_factory=JiraUserRegistry)
    github_users:  GitHubUserRegistry  = Field(default_factory=GitHubUserRegistry)

    # --- projects (one registry per source domain) ---
    git_projects:            GitProjectRegistry            = Field(default_factory=GitProjectRegistry)
    jira_projects:           JiraProjectRegistry           = Field(default_factory=JiraProjectRegistry)
    github_projects:         GitHubProjectRegistry         = Field(default_factory=GitHubProjectRegistry)
    code_structure_projects: CodeStructureProjectRegistry  = Field(default_factory=CodeStructureProjectRegistry)
    duplication_projects:    DuplicationProjectRegistry    = Field(default_factory=DuplicationProjectRegistry)
    quality_projects:        QualityProjectRegistry        = Field(default_factory=QualityProjectRegistry)
    lizard_projects:         LizardMetricsProjectRegistry  = Field(default_factory=LizardMetricsProjectRegistry)

    # --- git ---
    commits: CommitRegistry = Field(default_factory=CommitRegistry)
    files:   FileRegistry   = Field(default_factory=FileRegistry)
    changes: ChangeRegistry = Field(default_factory=ChangeRegistry)
    hunks:   HunkRegistry   = Field(default_factory=HunkRegistry)

    # --- jira ---
    issues:         IssueRegistry       = Field(default_factory=IssueRegistry)
    issue_statuses: IssueStatusRegistry = Field(default_factory=IssueStatusRegistry)
    issue_types:    IssueTypeRegistry   = Field(default_factory=IssueTypeRegistry)

    # --- github ---
    pull_requests:   PullRequestRegistry   = Field(default_factory=PullRequestRegistry)
    reviews:         ReviewRegistry        = Field(default_factory=ReviewRegistry)
    review_comments: ReviewCommentRegistry = Field(default_factory=ReviewCommentRegistry)
    github_commits:  GitHubCommitRegistry  = Field(default_factory=GitHubCommitRegistry)

    # --- code structure ---
    code_types:   CodeTypeRegistry      = Field(default_factory=CodeTypeRegistry)
    code_methods: CodeMethodRegistry    = Field(default_factory=CodeMethodRegistry)
    code_fields:  CodeFieldRegistry     = Field(default_factory=CodeFieldRegistry)
    code_refs:    CodeReferenceRegistry = Field(default_factory=CodeReferenceRegistry)

    # --- duplication / quality / lizard ---
    duplications:   DuplicationPairRegistry = Field(default_factory=DuplicationPairRegistry)
    quality_issues: QualityIssueRegistry    = Field(default_factory=QualityIssueRegistry)
    file_metrics:   FileMetricRegistry      = Field(default_factory=FileMetricRegistry)

    # --- components + enrichment outputs ---
    components:  ComponentRegistry  = Field(default_factory=ComponentRegistry)
    traits:      TraitRegistry      = Field(default_factory=TraitRegistry)
    classifiers: ClassifierRegistry = Field(default_factory=ClassifierRegistry)
    relations:   RelationRegistry   = Field(default_factory=RelationRegistry)

    # --- derived caches (excluded from serialization) ---
    # Lazy :class:`TemporalIndex` cache (Phase 2 decision D2). Built on
    # first call to :meth:`ensure_temporal_index`; survives until the
    # Graph instance is dropped. Excluded from pickling / dump because
    # it's a pure function of the commits registry.
    _temporal_index: Optional["TemporalIndex"] = PrivateAttr(default=None)

    # ------------------------------------------------------------------
    # Backwards-compat: accept the legacy ``registries=dict`` kwarg.
    # ------------------------------------------------------------------
    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_registries_dict(cls, data: Any) -> Any:
        """Fan a legacy ``registries={EntityKind: reg, ...}`` dict out into
        typed fields. Only triggered when ``data`` is a dict (the common
        construction path); class-instance / mapping paths flow through
        Pydantic's normal validation.

        Each key must be an :class:`EntityKind` that has exactly ONE
        matching typed field. ``EntityKind.PROJECT`` is rejected via this
        path — pass to a typed field explicitly (``git_projects=...``)
        instead — because the kernel has no way to pick one of the seven
        project registries from a single key.
        """
        if not isinstance(data, dict):
            return data
        if "registries" not in data:
            return data
        legacy = data.pop("registries")
        if legacy is None:
            return data
        if not isinstance(legacy, dict):
            raise TypeError(
                "Graph(registries=...) expects a dict[EntityKind, Registry], "
                f"got {type(legacy).__name__}"
            )
        for kind, registry in legacy.items():
            if not isinstance(kind, EntityKind):
                # Strings are tolerated — coerce.
                kind = EntityKind(kind)
            fields = _KIND_TO_FIELDS.get(kind, [])
            if not fields:
                raise ValueError(
                    f"Graph: unknown EntityKind {kind!r} in legacy "
                    f"registries dict"
                )
            if len(fields) > 1:
                raise ValueError(
                    f"Graph: EntityKind {kind!r} maps to multiple typed "
                    f"fields ({fields}); pass the typed field directly "
                    f"(e.g. {fields[0]}=...) instead of the legacy "
                    f"registries dict."
                )
            field_name = fields[0]
            if field_name in data:
                raise ValueError(
                    f"Graph: registries[{kind!r}] conflicts with explicit "
                    f"{field_name}= kwarg"
                )
            data[field_name] = registry
        return data

    # ------------------------------------------------------------------
    # Backwards-compat: ``g.registries`` as a read-only dict view.
    # ------------------------------------------------------------------
    @property
    def registries(self) -> Dict[EntityKind, "Registry[Any, Any]"]:
        """Dict-of-registries view of every typed registry field.

        Kept for Chunk 1/2 callers that iterate the dict. New code should
        read typed fields directly. ``EntityKind.PROJECT`` maps to the
        first non-empty project registry, or the ``git_projects``
        registry as a stable fallback — there's no unambiguous answer
        when multiple project registries exist, see Chunk 8 handoff
        "Open questions" §1.
        """
        out: Dict[EntityKind, "Registry[Any, Any]"] = {}
        for kind, fields in _KIND_TO_FIELDS.items():
            # Pick the first registry that contains entities; fall back to
            # the first declared field if all are empty.
            chosen = None
            for fname in fields:
                reg = getattr(self, fname)
                if len(reg) > 0:
                    chosen = reg
                    break
            if chosen is None:
                chosen = getattr(self, fields[0])
            out[kind] = chosen
        return out

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def registry_for(self, kind: EntityKind) -> Optional["Registry[Any, Any]"]:
        """Return the single registry that owns ``kind``, or ``None``.

        For kinds with exactly one matching field (the common case), the
        registry is returned even when it's empty — callers can ``add``
        to it. For ``EntityKind.PROJECT`` (which has 7 fields), the
        method returns ``None`` because there's no canonical choice;
        use :meth:`project_registries` or the typed field directly.
        """
        fields = _KIND_TO_FIELDS.get(kind, [])
        if len(fields) != 1:
            return None
        return getattr(self, fields[0])

    def resolve(self, ref: EntityRef) -> Optional[Entity]:
        """Resolve an ``EntityRef`` through the right registry. O(1).

        For ``EntityKind.PROJECT`` (which has 7 candidate registries),
        each project registry is consulted in declaration order and the
        first hit is returned.
        """
        fields = _KIND_TO_FIELDS.get(ref.kind, [])
        for fname in fields:
            reg = getattr(self, fname)
            entity = reg.get(ref.id)
            if entity is not None:
                return entity
        return None

    # ------------------------------------------------------------------
    # Project-specific helper (multiple registries share EntityKind.PROJECT)
    # ------------------------------------------------------------------
    def project_registries(self) -> Tuple["Registry[Any, Any]", ...]:
        """Every project-flavoured registry in declaration order."""
        return tuple(
            getattr(self, fname) for fname in _KIND_TO_FIELDS[EntityKind.PROJECT]
        )

    def add_project(self, project: Entity) -> Entity:
        """Add a :class:`Project` to the matching typed project registry.

        Dispatches by ``isinstance`` because every concrete project
        subclass declares ``kind = EntityKind.PROJECT``, so the
        ``ref.kind`` alone is ambiguous (7-way). ``isinstance`` checks
        each project field's declared registry class — the first match
        wins.
        """
        for fname in _KIND_TO_FIELDS[EntityKind.PROJECT]:
            reg: Registry[Any, Any] = getattr(self, fname)
            reg_cls = _FIELD_TO_CLS[fname]
            # Look up the Entity class the registry stores via its generic
            # argument. We use ``isinstance`` on the project itself against
            # the registry's documented entity type at registration time —
            # pragmatically, each *ProjectRegistry stores exactly one
            # concrete *Project class, and the project type is encoded in
            # the class name.
            #
            # ``Registry.get_id`` will accept any compatible project, so
            # we rely on the field-name convention: ``git_projects`` ⇒
            # GitProject, ``jira_projects`` ⇒ JiraProject, etc. The pair
            # is verified at module load (see ``_iter_project_specs``).
            entity_cls = _PROJECT_FIELD_TO_ENTITY_CLS.get(fname)
            if entity_cls is not None and isinstance(project, entity_cls):
                return reg.add(project)
        # Fallback: type didn't match any field — surface a clear error
        # rather than silently dropping it.
        raise TypeError(
            f"Graph.add_project: no matching project registry for "
            f"{type(project).__name__}. Update Graph._PROJECT_FIELD_TO_ENTITY_CLS "
            f"in src/common/kernel/graph.py."
        )

    # ------------------------------------------------------------------
    # Adding entities by kind (used by the processor dispatcher)
    # ------------------------------------------------------------------
    def add(self, entity: Entity) -> Entity:
        """Add an :class:`Entity` to its kind-matched registry.

        For non-project entities (single-field kinds), the dispatch is
        unambiguous. For projects, :meth:`add_project` is delegated to.
        """
        kind = type(entity).kind
        if kind == EntityKind.PROJECT:
            return self.add_project(entity)
        reg = self.registry_for(kind)
        if reg is None:
            raise ValueError(
                f"Graph.add: no registry for kind {kind!r} "
                f"(entity={type(entity).__name__})"
            )
        return reg.add(entity)

    # ------------------------------------------------------------------
    # Derived caches
    # ------------------------------------------------------------------
    def ensure_temporal_index(self) -> "TemporalIndex":
        """Return a :class:`TemporalIndex` built lazily over commit
        timestamps; cached on the instance after first call.

        Per Phase 2 decision D2 (the locked API for Chunk 11). The cache
        is intentionally NOT invalidated on commit-registry mutations:
        v2 builds the commits registry once during the transformer phase
        and never appends mid-pipeline. If a future workflow needs
        cache invalidation, expose a ``reset_temporal_index()`` method
        here — do NOT inline the build cost into every consumer.
        """
        if self._temporal_index is None:
            # Local import keeps the kernel ↔ enrichment cycle broken at
            # module-load time: enrichment.utils imports from the kernel,
            # so the kernel can only import enrichment lazily.
            from ...enrichment.utils.temporal import TemporalIndex

            self._temporal_index = TemporalIndex.from_graph(self)
        return self._temporal_index

    # ------------------------------------------------------------------
    # IO
    # ------------------------------------------------------------------
    def dump(self, store: "PickleStore") -> None:
        """Persist every registry field to ``store``.

        One ``<name>.pkl`` per registry + a ``meta.json``. Field names
        are mapped to stable on-disk names via :data:`_FIELD_TO_DISK`
        (independent from the Python attribute names so we can rename
        fields without invalidating pickles).
        """
        for fname, disk_name in _FIELD_TO_DISK.items():
            registry: Registry[Any, Any] = getattr(self, fname)
            store.write_registry(disk_name, registry)
        store.meta_write(
            {
                "schema_version": self.schema_version,
                "project_id": self.project_id,
                "built_at": self.built_at.isoformat(),
                "registries": sorted(_FIELD_TO_DISK.values()),
            }
        )

    @classmethod
    def lazy(
        cls,
        project_id: str,
        store: "PickleStore",
    ) -> "Graph":
        """Build a :class:`Graph` whose registry fields are lazy proxies.

        Each typed field is filled with a
        :class:`~src.common.pickle_store.LazyRegistryProxy` instance
        (via :func:`lazy_proxy_for`) that loads the on-disk pickle on
        first use. Schema version is enforced as in Chunk 1.
        """
        # Local import to avoid a kernel ↔ pickle_store cycle.
        from ..pickle_store import lazy_proxy_for

        meta = store.meta_read() or {}

        stored_version = meta.get("schema_version")
        if stored_version is not None and stored_version != SCHEMA_VERSION:
            raise ValueError(
                f"PickleStore at {store.base_dir} has schema_version="
                f"{stored_version!r}, kernel expects {SCHEMA_VERSION}. "
                "Re-run `Build Graph` from the web UI to regenerate."
            )

        built_at_raw = meta.get("built_at")
        built_at = (
            datetime.fromisoformat(built_at_raw)
            if isinstance(built_at_raw, str)
            else datetime.now(timezone.utc)
        )

        # Build a kwargs dict: every typed field gets a lazy proxy bound
        # to its on-disk name + registry class. Pydantic accepts the
        # proxy because each ``lazy_proxy_for(RegCls, ...)`` returns an
        # instance that ``isinstance``-matches ``RegCls`` (see
        # ``pickle_store.lazy_proxy_for`` doctring).
        kwargs: Dict[str, Any] = {
            "project_id": project_id,
            "built_at": built_at,
        }
        for fname, disk_name in _FIELD_TO_DISK.items():
            reg_cls = _FIELD_TO_CLS[fname]
            loader = _make_loader(store, disk_name, reg_cls)
            kwargs[fname] = lazy_proxy_for(reg_cls, store, disk_name, loader)

        return cls(**kwargs)


# ---------------------------------------------------------------------------
# Internal helpers (module-private)
# ---------------------------------------------------------------------------
def _make_loader(
    store: "PickleStore",
    disk_name: str,
    reg_cls: type,
) -> Callable[[], "Registry[Any, Any]"]:
    """Return a closure that loads ``disk_name`` as ``reg_cls`` from ``store``.

    Defined at module scope so the closure captures only the three
    arguments — no reference to ``Graph`` (which keeps Pickling stable
    if a proxy ever leaks into a pickled value).

    If the on-disk file is missing, the loader returns a fresh empty
    instance of ``reg_cls``: this is the greenfield case where the
    project was built before a new registry was added, and the rest of
    the pickle is still valid.
    """
    def _load() -> "Registry[Any, Any]":
        # Late import to avoid the kernel ↔ pickle_store cycle.
        from ..pickle_store import PickleStore  # noqa: F401

        try:
            return store.read_registry(disk_name, reg_cls)
        except FileNotFoundError:
            return reg_cls()
    return _load


# ---------------------------------------------------------------------------
# Project field → entity class (used by Graph.add_project).
# ---------------------------------------------------------------------------
# Imported lazily to keep the typed-fields block self-contained above.
def _iter_project_specs() -> Iterable[tuple[str, type[Entity]]]:
    from ..domains.git.models import GitProject
    from ..domains.jira.models import JiraProject
    from ..domains.github.models import GitHubProject
    from ..domains.code_structure.models import CodeStructureProject
    from ..domains.duplication.models import DuplicationProject
    from ..domains.quality.models import QualityProject
    from ..domains.metrics_lizard.models import LizardMetricsProject

    yield "git_projects",            GitProject
    yield "jira_projects",           JiraProject
    yield "github_projects",         GitHubProject
    yield "code_structure_projects", CodeStructureProject
    yield "duplication_projects",    DuplicationProject
    yield "quality_projects",        QualityProject
    yield "lizard_projects",         LizardMetricsProject


_PROJECT_FIELD_TO_ENTITY_CLS: Dict[str, type[Entity]] = dict(_iter_project_specs())


__all__ = ["Graph", "SCHEMA_VERSION"]
