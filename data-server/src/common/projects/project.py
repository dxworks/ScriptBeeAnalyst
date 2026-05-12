"""Project base — metadata-only ancestor for every source project.

See §3 of ``architectural_changes.md``. Concrete subclasses (``GitProject``,
``JiraProject``, ``GitHubProject``, ``CodeStructureProject``, …) live in
their per-domain modules (Chunks 4+). The Project's job is *metadata only*:
it no longer owns its entities. Each entity carries ``project_ref:
EntityRef`` instead; "all commits in this git project" is then
``graph.commits.by_project[git_project.ref()]``.

Subclass contract::

    class GitProject(Project):
        kind: ClassVar[EntityKind] = EntityKind.PROJECT
        # GitProject-specific metadata fields go here

        def transformer_class(self) -> type["Transformer"]:
            from ..domains.git.transformer import GitTransformer
            return GitTransformer

``transformer_class`` returns a type — forward-referenced as the string
``"Transformer"`` to keep this module free of dependencies on the
not-yet-written enrichment layer.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import List

from ..kernel import Entity, IndexSpec, Registry
from ..people.source import SourceKind


class Project(Entity, abstract=True):
    """Abstract base for every source-side ``Project`` entity.

    Attributes
    ----------
    name:
        Human-facing project name from the source.
    source:
        :class:`SourceKind` discriminator. Repeats information that
        ``kind``/subclass already encodes, but is also the key for the
        registry's ``by_source`` index — that's the main query the MCP
        sandbox + the UI use ("show me the git project of this graph").
    linked_project_ids:
        Plain string ids of other Projects this one is linked to. They
        resolve through :class:`ProjectRegistry` by id; no need for
        ``EntityRef`` since every Project shares the same registry.
    """

    name: str
    source: SourceKind
    linked_project_ids: List[str] = []

    @abstractmethod
    def transformer_class(self) -> type["Transformer"]:  # noqa: F821
        """Return the :class:`Transformer` subclass that produced this project.

        Forward-referenced; the actual ``Transformer`` base lives in a later
        chunk (enrichment layer). Concrete projects implement this by
        importing their per-domain transformer lazily inside the method.
        """


class ProjectRegistry(Registry[Project, str]):
    """Holds every concrete :class:`Project` in the graph.

    A single, generic registry is enough because Projects are pure metadata
    (§3) — they don't need per-domain CRUD. The ``by_source`` index makes
    "give me the git project" / "the jira project" / "every project for
    this source" an O(1) lookup, which the MCP sandbox + smart-merge UI
    both rely on.
    """

    indexes = [
        IndexSpec(name="by_source", key_fn=lambda p: p.source, multi=True),
    ]

    def get_id(self, entity: Project) -> str:
        return entity.id


__all__ = ["Project", "ProjectRegistry"]
