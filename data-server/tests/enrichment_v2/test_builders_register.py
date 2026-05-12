"""Every relation-builder implementation registers with :data:`BUILDERS`.

Importing the package side-loads each module via the package's
``__init__`` import side-effects; each module decorates its
:class:`RelationBuilder` subclass with ``@BUILDERS.register``. This test
asserts:

* The catalog is non-empty after the import.
* Every expected legacy-port name is present.
* No duplicate registration (``BUILDERS`` raises if two different
  classes share a name, which would surface here as an ImportError).
"""
from __future__ import annotations

import src.enrichment.relations_v2.implementations  # noqa: F401 — side-effect import
from src.enrichment.relations_v2 import BUILDERS, BuilderRegistry, RelationBuilder


# Names we expect to find in the catalog. Cross-checked against the
# Chunk-7 brief.
_EXPECTED_NAMES = {
    # Substantively-ported builders.
    "coauthor",
    "cochange",
    "ownership",
    "calls",
    "coupling",
    "data_access",
    "hierarchy",
    "duplication.external",
    "duplication.sibling",
    "duplication.internal_summary",
    "issue.file",
    "issue.issue",
    "pr.file",
    "pr.issue",
    "pr.reviewer",
    # Deferred-stub builders (NotImplementedError in build()).
    "cochange.file_time_windowed",
    "cochange.file_shared_devs",
    "cochange.file_shared_task_prefixes",
    "cochange.component",
    "cochange.component_shared_devs",
    "cochange.component_shared_task_prefixes",
    "cochange.component_time_windowed",
    "cochange.author_shared_task_prefixes",
    "cochange.author_time_windowed",
    "similarity.file_names",
}


def test_builder_registry_singleton_is_a_builder_registry() -> None:
    assert isinstance(BUILDERS, BuilderRegistry)


def test_every_implementation_registers() -> None:
    names = set(BUILDERS.names())
    assert _EXPECTED_NAMES.issubset(names), (
        f"Missing: {_EXPECTED_NAMES - names}"
    )


def test_every_registered_class_subclasses_relation_builder() -> None:
    for cls in BUILDERS:
        assert issubclass(cls, RelationBuilder), cls
        # ABC discipline — the abstract ``build`` must be implemented in
        # every concrete subclass (else instantiation would fail).
        assert "build" in {
            attr for attr in dir(cls) if not attr.startswith("_")
        }


def test_builder_registry_catalog_size_at_least_25() -> None:
    """Total builders shipped: 25.

    * 24 builders were the round-1 count — one per legacy
      ``relations/*.py`` file (10 cochange flavours + 14 others).
    * Review round 2 added :class:`PrIssueBuilder` (``name="pr.issue"``,
      ``relation_kind="pr_issue"``) to subsume the legacy
      ``ProjectLinker.link_pull_requests_with_issues`` mutation. Total
      now 25.
    * Chunk 8 may add additional builders; this asserts a floor, not
      an exact count.
    """
    assert len(BUILDERS) >= 25


def test_decorator_style_registration_returns_class() -> None:
    """``@BUILDERS.register`` must return the decorated class (not None)."""
    isolated = BuilderRegistry()

    @isolated.register
    class _LocalBuilder(RelationBuilder):
        name = "test.decorator_round_trip"
        relation_kind = "x"

        def build(self, graph):
            return ()

    # The decorator preserved the class reference.
    assert _LocalBuilder is not None
    assert isolated.get("test.decorator_round_trip") is _LocalBuilder
