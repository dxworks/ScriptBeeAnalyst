"""Registries for every code-structure-domain :class:`Entity` subclass.

Each registry declares the secondary indexes Chunk 7 (relation builders) and
the MCP sandbox helpers will actually use. Per plan §1.5, indexes are
declared as a ``ClassVar[list[IndexSpec]]`` and rebuilt on every mutation /
on :meth:`Registry.load` — they are NOT pickled.
"""
from __future__ import annotations

from typing import Optional

from ...kernel import IndexSpec, Registry
from .models import (
    CodeField,
    CodeMethod,
    CodeReference,
    CodeStructureProject,
    CodeType,
)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class CodeStructureProjectRegistry(Registry[CodeStructureProject, str]):
    """Holds every :class:`CodeStructureProject` in the graph.

    Same shape as :class:`git.GitProjectRegistry` — domain-specific typing
    helper. At the graph level, all :class:`Project` subclasses may share a
    single :class:`ProjectRegistry` (plan §3 + Chunk 2 design choice §5);
    Chunk 8 decides whether to merge.

    Indexes:

    * ``by_name``           — "the code-structure project named X".
    * ``by_kind_of_source`` — quick filter for "all JaFax projects" vs
                              "all Codeframe projects" (task 8 needs this
                              when the flip is rolled out).
    """

    indexes = [
        IndexSpec(name="by_name", key_fn=lambda p: p.name, multi=True),
        IndexSpec(
            name="by_kind_of_source",
            key_fn=lambda p: p.kind_of_source,
            multi=True,
        ),
    ]

    def get_id(self, entity: CodeStructureProject) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Code types
# ---------------------------------------------------------------------------


def _type_file_key(t: CodeType):
    """Skip ``None`` file_refs from the by_file index."""
    return t.file_ref


class CodeTypeRegistry(Registry[CodeType, str]):
    """Every :class:`CodeType` in the graph.

    Indexes (plan §4.1 + handoff):

    * ``by_file``        — fast "all types declared in file F" lookup.
                           ``None`` keys are skipped (external types).
    * ``by_project``     — one bucket per :class:`CodeStructureProject`.
    * ``by_simple_name`` — "all `Foo` classes across the project". The
                           legacy ``name`` field used to back this.
    * ``by_fqn``         — exact lookup by fully-qualified name (so
                           inheritance resolution can find a class by FQN
                           in O(1)).
    """

    indexes = [
        IndexSpec(name="by_file", key_fn=_type_file_key, multi=True),
        IndexSpec(name="by_project", key_fn=lambda t: t.project_ref, multi=True),
        IndexSpec(
            name="by_simple_name", key_fn=lambda t: t.simple_name, multi=True
        ),
        IndexSpec(
            name="by_fqn", key_fn=lambda t: t.fully_qualified_name, multi=True
        ),
    ]

    def get_id(self, entity: CodeType) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Code methods
# ---------------------------------------------------------------------------


def _method_type_key(m: CodeMethod) -> Optional["object"]:
    return m.type_ref


class CodeMethodRegistry(Registry[CodeMethod, str]):
    """Every :class:`CodeMethod` in the graph.

    Indexes:

    * ``by_type``    — fast "all methods of type T". ``None`` skipped
                       (orphan methods).
    * ``by_project`` — one bucket per :class:`CodeStructureProject`.
    * ``by_name``    — "all methods named ``equals``", useful for
                       cross-type signature checks and metrics.
    """

    indexes = [
        IndexSpec(name="by_type", key_fn=_method_type_key, multi=True),
        IndexSpec(name="by_project", key_fn=lambda m: m.project_ref, multi=True),
        IndexSpec(name="by_name", key_fn=lambda m: m.name, multi=True),
    ]

    def get_id(self, entity: CodeMethod) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Code fields
# ---------------------------------------------------------------------------


def _field_type_key(f: CodeField) -> Optional["object"]:
    return f.type_ref


class CodeFieldRegistry(Registry[CodeField, str]):
    """Every :class:`CodeField` in the graph.

    Indexes:

    * ``by_type``    — "all fields of type T". ``None`` skipped.
    * ``by_project`` — one bucket per :class:`CodeStructureProject`.
    * ``by_name``    — "all fields named ``id`` / ``size``", for naming-
                       convention metrics.
    """

    indexes = [
        IndexSpec(name="by_type", key_fn=_field_type_key, multi=True),
        IndexSpec(name="by_project", key_fn=lambda f: f.project_ref, multi=True),
        IndexSpec(name="by_name", key_fn=lambda f: f.name, multi=True),
    ]

    def get_id(self, entity: CodeField) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Code references
# ---------------------------------------------------------------------------


def _ref_source_key(r: CodeReference):
    """Fan-out key picking the non-``None`` source ref.

    A :class:`CodeReference` row always sets exactly one of
    ``source_method_ref`` / ``source_type_ref`` (see model docstring); the
    by-source index keys on whichever one is set so consumers don't have
    to inspect the kind discriminator first. ``None`` skips the row.
    """
    return r.source_method_ref or r.source_type_ref


def _ref_target_key(r: CodeReference):
    """Fan-out key picking the non-``None`` target ref."""
    return r.target_method_ref or r.target_type_ref or r.target_field_ref


class CodeReferenceRegistry(Registry[CodeReference, str]):
    """Every :class:`CodeReference` row in the graph.

    Indexes:

    * ``by_source``  — "all references originating from X" (X may be a
                       method or a type — see ``_ref_source_key``).
    * ``by_target``  — "all references pointing at Y" (Y may be a
                       method, type, or field — see ``_ref_target_key``).
    * ``by_kind``    — "all calls" / "all inheritance edges" etc.
    * ``by_project`` — one bucket per :class:`CodeStructureProject`.
    """

    indexes = [
        IndexSpec(name="by_source", key_fn=_ref_source_key, multi=True),
        IndexSpec(name="by_target", key_fn=_ref_target_key, multi=True),
        IndexSpec(name="by_kind", key_fn=lambda r: r.reference_kind, multi=True),
        IndexSpec(name="by_project", key_fn=lambda r: r.project_ref, multi=True),
    ]

    def get_id(self, entity: CodeReference) -> str:
        return entity.id


__all__ = [
    "CodeFieldRegistry",
    "CodeMethodRegistry",
    "CodeReferenceRegistry",
    "CodeStructureProjectRegistry",
    "CodeTypeRegistry",
]
