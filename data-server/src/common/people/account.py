"""Account base — common ancestor for every source-side user/contributor.

See §2.1 of ``architectural_changes.md``. The hierarchy::

    Account (abstract, Entity subclass)
      ├── GitAccount   (kind=GIT_ACCOUNT,   source=GIT,    email)      ← Chunk 4
      ├── JiraUser     (kind=JIRA_USER,     source=JIRA,   key, link)  ← Chunk 5
      └── GitHubUser   (kind=GITHUB_USER,   source=GITHUB, login, url) ← Chunk 6

All three sub-domains share:

* ``name`` — display name carried over from the raw source.
* ``project_ref`` — typed :class:`EntityRef` to the originating source
  ``Project`` (e.g. a ``GitProject`` ref, not a Python object).
* ``unified_user_id`` — populated by the smart-merge UI after a user accepts
  the suggestion. ``None`` until then; this is the cheap "back-pointer" to
  the :class:`UnifiedUser` registry.
* ``source`` — abstract property; each concrete subclass returns its
  :class:`SourceKind` unambiguously (no graph round-trip, no ``project_ref``
  resolve). Per plan §2.1.

Concrete fields (``email``/``key``/``login``) live on the per-domain
subclasses — this module deliberately does NOT import them so Chunk 2 stays
decoupled from Chunks 4/5/6.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Optional

from ..kernel import Entity, EntityRef
from .source import SourceKind


class Account(Entity, abstract=True):
    """Abstract base for every source-side account entity.

    Subclasses MUST declare:

    * ``kind: ClassVar[EntityKind] = …`` (enforced by the kernel's
      ``__init_subclass__``).
    * ``source`` — either as an overridden ``@property`` or simply as a
      class attribute ``source = SourceKind.<X>``. Forgetting it raises
      ``TypeError`` at instantiation time (Python's ABC machinery).
    """

    name: str
    project_ref: EntityRef
    unified_user_id: Optional[str] = None

    @property
    @abstractmethod
    def source(self) -> SourceKind:
        """The :class:`SourceKind` this account belongs to.

        Kept cheap and local on the subclass — do NOT derive from
        ``project_ref`` (no graph access, no resolve cost). Concrete
        subclasses usually override with a one-liner property or a class
        attribute::

            class GitAccount(Account):
                kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT

                @property
                def source(self) -> SourceKind:
                    return SourceKind.GIT
        """


__all__ = ["Account"]
