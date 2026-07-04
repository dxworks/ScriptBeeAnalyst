"""Project base + ProjectRegistry — abstractness, source field, by_source index."""
from __future__ import annotations

from typing import ClassVar

import pytest

from src.common.kernel import EntityKind
from src.common.people import SourceKind
from src.common.projects import Project, ProjectRegistry


# ---- abstractness -------------------------------------------------------


def test_project_concrete_subclass_without_kind_is_rejected():
    """Project is abstract; a concrete leaf must declare ``kind``."""
    with pytest.raises(TypeError, match="must declare"):

        class _BadProject(Project):  # noqa: F841 — must raise on class creation
            def transformer_class(self) -> type:  # type: ignore[override]
                return type


def test_project_is_marked_abstract():
    """Sanity: ``abstract=True`` propagated to the Entity flag."""
    assert getattr(Project, "__entity_abstract__", False) is True


def test_concrete_project_with_kind_and_source_works():
    """A correctly-declared concrete Project subclass behaves like an Entity."""

    class _Transformer:
        """Stand-in for the not-yet-written Transformer base."""

    class _GitProject(Project):
        kind: ClassVar[EntityKind] = EntityKind.PROJECT

        def transformer_class(self) -> type["Transformer"]:  # noqa: F821
            return _Transformer

    proj = _GitProject(
        id="git-proj-1",
        name="zeppelin",
        source=SourceKind.GIT,
    )
    assert proj.id == "git-proj-1"
    assert proj.name == "zeppelin"
    assert proj.source == SourceKind.GIT
    assert proj.linked_project_ids == []
    assert proj.transformer_class() is _Transformer


def test_project_linked_project_ids_are_plain_strings():
    """`linked_project_ids` is `list[str]` per plan — they all resolve via
    ProjectRegistry, no EntityRef needed."""

    class _JiraProject(Project):
        kind: ClassVar[EntityKind] = EntityKind.PROJECT

        def transformer_class(self) -> type:
            return type

    proj = _JiraProject(
        id="jira-1",
        name="Zeppelin Jira",
        source=SourceKind.JIRA,
        linked_project_ids=["git-1", "github-1"],
    )
    assert proj.linked_project_ids == ["git-1", "github-1"]


# ---- registry behaviors -------------------------------------------------


class _Project(Project):
    """Test-only concrete Project. Mirrors what Chunks 4+ will declare."""

    kind: ClassVar[EntityKind] = EntityKind.PROJECT

    def transformer_class(self) -> type:
        return type


def test_project_registry_add_get():
    reg = ProjectRegistry()
    proj = _Project(id="git-1", name="g", source=SourceKind.GIT)
    reg.add(proj)
    assert reg.get("git-1") is proj
    assert "git-1" in reg
    assert len(reg) == 1


def test_project_registry_by_source_index_lookup():
    """One bucket per SourceKind value."""
    reg = ProjectRegistry()
    reg.add(_Project(id="git-1", name="zep-git", source=SourceKind.GIT))
    reg.add(_Project(id="jira-1", name="zep-jira", source=SourceKind.JIRA))
    reg.add(_Project(id="git-2", name="zep-git-2", source=SourceKind.GIT))

    git_projects = reg.by_source[SourceKind.GIT]
    assert {p.id for p in git_projects} == {"git-1", "git-2"}

    jira_projects = reg.by_source[SourceKind.JIRA]
    assert {p.id for p in jira_projects} == {"jira-1"}

    # Empty bucket for a SourceKind nobody added.
    assert reg.by_source[SourceKind.GITHUB] == ()


def test_project_registry_remove_updates_by_source():
    reg = ProjectRegistry()
    reg.add(_Project(id="git-1", name="g1", source=SourceKind.GIT))
    reg.add(_Project(id="git-2", name="g2", source=SourceKind.GIT))
    reg.remove("git-1")
    assert {p.id for p in reg.by_source[SourceKind.GIT]} == {"git-2"}
