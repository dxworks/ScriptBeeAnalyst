"""Git-domain entities for the v2 graph.

Subclasses every cross-entity reference uses :class:`EntityRef` — never a
Python object — per plan §4. Field shapes are a faithful port of the legacy
``src/common/git_models.py``; see the Chunk 4 handoff for the
field-by-field mapping table.

Entity-vs-value-object decisions (see plan §1.1 + handoff):

* :class:`Hunk` is an :class:`Entity` (``EntityKind.HUNK`` is listed in the
  kernel kinds set). It carries a stable composite id and a
  ``change_ref``.
* :class:`LineChange` is a value object (no ``LINE_CHANGE`` kind exists).
  Nested inside :class:`Hunk.line_changes`.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, List, Optional

from pydantic import BaseModel, ConfigDict

from ...kernel import Entity, EntityKind, EntityRef
from ...people import Account, SourceKind
from ...projects import Project

if TYPE_CHECKING:  # forward-only — keeps cycles broken
    from .transformer import GitTransformer  # noqa: F401


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class LineOperation(StrEnum):
    """Direction of a single line edit inside a hunk.

    Faithful port of the legacy ``LineOperation`` (was a plain ``Enum`` with
    ``"ADD"`` / ``"DELETE"`` values). Promoted to ``StrEnum`` for consistency
    with the rest of the v2 kernel (`EntityKind`, `SourceKind`).
    """

    ADD = "ADD"
    DELETE = "DELETE"


class ChangeType(StrEnum):
    """Kind of file-level change inside a commit.

    Mirrors the legacy ``ChangeType`` (``ADD`` / ``DELETE`` / ``RENAME`` /
    ``MODIFY``) — these are the four values the inspector-git reader emits
    (`reader/enums/chnage_type.py`). The plan mentions wider categories
    (`copied` / `typechanged`) but the actual miner never produces them,
    so we keep parity with what the wire format carries.
    """

    ADD = "ADD"
    DELETE = "DELETE"
    RENAME = "RENAME"
    MODIFY = "MODIFY"


# ---------------------------------------------------------------------------
# Value objects (NOT entities — nested inside `Hunk`)
# ---------------------------------------------------------------------------


class LineChange(BaseModel):
    """A single added / deleted line inside a :class:`Hunk`.

    Value object — no ``LINE_CHANGE`` member exists in :class:`EntityKind`.
    Per plan §4.1: ``LineChange`` is intentionally not an Entity. The legacy
    model carried a back-pointer ``commit: GitCommit`` (a cycle); we replace
    it with a typed :class:`EntityRef` so pickling stays cycle-free.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    operation: LineOperation
    line_number: int
    commit_ref: EntityRef


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class GitProject(Project):
    """A single Git repository's metadata.

    Per plan §3, ``Project`` is metadata only — entities live in flat
    registries at the top of :class:`Graph` and carry ``project_ref``. The
    legacy ``GitProject`` owned four registries (account/commit/file/change);
    that ownership moves to :class:`Graph` in Chunk 8.

    The only non-base field we port is ``name`` (already on :class:`Project`).
    Any future Git-specific metadata (``remote_url`` / ``default_branch``)
    would land here.
    """

    kind: ClassVar[EntityKind] = EntityKind.PROJECT

    def transformer_class(self) -> type["GitTransformer"]:  # type: ignore[override]
        # Lazy import keeps the project module free of the transformer
        # module's own kernel/people dependencies and matches the suggested
        # pattern in the Chunk 2 handoff.
        from .transformer import GitTransformer

        return GitTransformer


class GitAccount(Account):
    """Author or committer signature seen in a Git history.

    Field mapping vs legacy ``GitAccount``:

    * ``id``               — was the str-cast of ``GitAccountId`` (``"Name
                             <email>"``). We keep that exact composite as the
                             primary id so cross-refs stay stable.
    * ``email``            — was ``git_id.email``.
    * ``name``             — inherited from :class:`Account`; was
                             ``git_id.name`` (legacy hoisted it via a
                             ``model_validator``).
    * ``project_ref``      — was ``project: Project`` (Python ref). Now a
                             typed :class:`EntityRef` to the owning
                             ``GitProject``.
    * ``unified_user_id``  — inherited from :class:`Account`; was absent on
                             the legacy ``GitAccount`` (developer linkage
                             went through ``Developer.accounts``).
    * ``commits``          — DROPPED. Reverse lookup goes through
                             :class:`CommitRegistry.by_author` /
                             ``by_committer``.
    """

    kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
    # ``source`` is declared on the legacy ``Account`` ABC as an abstract
    # property; the Chunk 2 handoff documents that concrete leaves may
    # override it as a class attribute. We use a class attr to keep the
    # access cheap and emphasize per-class-constant semantics.
    source: ClassVar[SourceKind] = SourceKind.GIT  # type: ignore[misc]

    email: str

    @staticmethod
    def make_id(name: str, email: str) -> str:
        """Canonical composite id matching legacy ``str(GitAccountId)``."""
        return f"{name} <{email}>"


class Commit(Entity):
    """A single Git commit.

    Field mapping vs legacy ``GitCommit``:

    * ``id``                   — repo-scoped composite ``f"{repo_name}:{sha}"``
                                  built via :meth:`Commit.make_id`. Pre-F1
                                  this was the bare SHA, but two repos in
                                  the same graph can share subtree SHAs,
                                  which would silently overwrite registry
                                  entries. The scoped id keeps both repos
                                  addressable while leaving cross-source
                                  joins (GitHub.sha → git Commit) routed
                                  through the :class:`CommitRegistry.by_sha`
                                  index, not through string id equality.
    * ``sha``                  — explicit copy of the bare SHA; same value
                                  as the suffix of ``id`` but kept as a
                                  named field so :class:`CommitRegistry`
                                  can index by it and downstream code can
                                  read ``commit.sha`` without parsing the
                                  id.
    * ``project_ref``          — was ``project: Optional[GitProject]``.
    * ``message``              — unchanged.
    * ``author_date``          — unchanged.
    * ``committer_date``       — unchanged.
    * ``author_ref``           — was ``author: Optional[GitAccount]``.
    * ``committer_ref``        — was ``committer: Optional[GitAccount]``.
    * ``parent_refs``          — was ``parents: List[GitCommit]``.
    * ``branch_id`` / ``repo_size`` — unchanged.
    * ``changes``              — DROPPED. Look up via
                                  :class:`ChangeRegistry.by_commit`.
    * ``children``             — DROPPED. Look up via
                                  :class:`CommitRegistry.by_parent` (reverse
                                  of ``parent_refs``).
    * ``issues`` / ``pull_requests`` — DROPPED (cross-source links). Move
                                  into :class:`RelationRegistry` (Chunk 7).
    """

    kind: ClassVar[EntityKind] = EntityKind.COMMIT

    project_ref: EntityRef
    sha: str
    message: str
    author_date: datetime
    committer_date: datetime
    author_ref: EntityRef
    committer_ref: EntityRef
    parent_refs: List[EntityRef] = []
    branch_id: int = 0
    repo_size: int = 0

    @staticmethod
    def make_id(repo_name: str, sha: str) -> str:
        """Repo-scoped commit id. ``{repo_name}:{sha}``."""
        return f"{repo_name}:{sha}"


class File(Entity):
    """A file tracked across a Git history.

    Field mapping vs legacy ``File``:

    * ``id``               — repo-scoped composite ``f"{repo_name}::{path}"``
                             built via :meth:`File.make_id` (F1). The bare
                             ``path`` remains the natural identifier within
                             a single repo; the ``::`` separator avoids
                             colliding with ``Commit.id``'s single-colon
                             convention. Pre-F1 this was just the path;
                             two repos uploaded to the same project that
                             share file paths (extremely common — every
                             ``README.md`` / ``pom.xml`` / ``Makefile``)
                             would overwrite each other in the registry.
    * ``project_ref``      — was ``project: Optional[GitProject]``.
    * ``path``             — bare path, no repo prefix. Use this for
                             extension extraction, dirname comparisons,
                             external joins (Lizard CSV / JaFax JSON /
                             DuDe CSV all carry bare paths). The
                             ``project_ref`` disambiguates which repo
                             this path belongs to.
    * ``is_binary``        — unchanged.
    * ``changes``          — DROPPED. Reverse lookup via
                             :class:`ChangeRegistry.by_file`.
    * ``extension``        — NEW; computed once at construction so the
                             :class:`FileRegistry.by_extension` index has a
                             cheap key. Derived from ``path``.

    Legacy ``File`` carried many derived helpers (``annotated_lines`` /
    ``relative_path`` / ``last_existing_name``); those move to the
    enrichment / sandbox layer in later chunks.
    """

    kind: ClassVar[EntityKind] = EntityKind.FILE

    project_ref: EntityRef
    path: str
    is_binary: bool = False
    extension: Optional[str] = None

    @staticmethod
    def make_id(repo_name: str, path: str) -> str:
        """Repo-scoped file id. ``{repo_name}::{path}``.

        The double-colon separator (vs Commit's single colon) keeps the
        two namespaces visually distinct and avoids ambiguity when a
        path happens to start with a single colon-prefixed segment.
        """
        return f"{repo_name}::{path}"

    @staticmethod
    def derive_extension(path: str) -> Optional[str]:
        """Return everything after the last '.' in the basename, or None."""
        if not path:
            return None
        slash = path.rfind("/")
        basename = path[slash + 1 :] if slash != -1 else path
        dot = basename.rfind(".")
        if dot <= 0:  # no dot, or starts with '.'
            return None
        return basename[dot + 1 :]


class Change(Entity):
    """A single file change inside a :class:`Commit`.

    Field mapping vs legacy ``Change``:

    * ``id``                  — unchanged (composite of commit id + path).
    * ``commit_ref``          — was ``commit: Optional[GitCommit]``.
    * ``file_ref``            — was ``file: Optional[File]``.
    * ``change_type``         — unchanged enum value, now :class:`ChangeType`.
    * ``old_path`` / ``new_path``
                              — were ``old_file_name`` / ``new_file_name``;
                              renamed for clarity (paths, not bare names).
    * ``parent_commit_ref``   — was ``parent_commit: Optional[GitCommit]``.
    * ``parent_change_ref``   — was ``parent_change: Optional[Change]``.
    * ``hunks``               — NEW: list of :class:`EntityRef` to
                                :class:`Hunk` entities (Hunks are
                                first-class).
    * ``annotated_lines`` /
      ``compute_annotated_lines`` — DROPPED. These were enrichment-time
                                concerns and rebuild from the hunk stream
                                if a metric needs them.
    """

    kind: ClassVar[EntityKind] = EntityKind.CHANGE

    commit_ref: EntityRef
    file_ref: EntityRef
    change_type: ChangeType
    old_path: str
    new_path: str
    parent_commit_ref: Optional[EntityRef] = None
    parent_change_ref: Optional[EntityRef] = None
    hunk_refs: List[EntityRef] = []

    @staticmethod
    def make_id(commit_id: str, old_path: str, new_path: str) -> str:
        """Canonical composite id matching legacy ``Change._id``."""
        return f"{commit_id}-{old_path}->{new_path}"


class Hunk(Entity):
    """A contiguous range of edits inside a :class:`Change`.

    Promoted to an :class:`Entity` (``EntityKind.HUNK``) per plan §1.1. The
    legacy ``Hunk`` was a value object nested inside ``Change.hunks``; in
    v2 we keep :class:`LineChange` nested but pull the Hunk itself out so
    metrics + relation builders can attach traits / relations to specific
    hunks (e.g. "this hunk was reviewed in PR X"). The composite id is
    deterministic so re-running the transformer doesn't churn graph ids.

    Field mapping vs legacy ``Hunk``:

    * ``id``                  — NEW: ``"{change_id}#{ordinal}"``.
    * ``change_ref``          — NEW: typed :class:`EntityRef`.
    * ``ordinal``             — NEW: position within the change (0-based).
    * ``line_changes``        — unchanged; value objects nested here.

    ``Hunk.deleted_lines`` / ``Hunk.added_lines`` are exposed as cheap
    computed properties (legacy stored them as separate fields).
    """

    kind: ClassVar[EntityKind] = EntityKind.HUNK

    change_ref: EntityRef
    ordinal: int
    line_changes: List[LineChange] = []

    @staticmethod
    def make_id(change_id: str, ordinal: int) -> str:
        return f"{change_id}#{ordinal}"

    @property
    def added_lines(self) -> List[LineChange]:
        return [lc for lc in self.line_changes if lc.operation == LineOperation.ADD]

    @property
    def deleted_lines(self) -> List[LineChange]:
        return [lc for lc in self.line_changes if lc.operation == LineOperation.DELETE]


__all__ = [
    "ChangeType",
    "LineOperation",
    "LineChange",
    "GitProject",
    "GitAccount",
    "Commit",
    "File",
    "Change",
    "Hunk",
]
