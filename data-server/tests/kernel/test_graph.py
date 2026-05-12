"""Graph root sanity: registry_for / resolve / dump+lazy."""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import pytest

from src.common.kernel import (
    Entity,
    EntityKind,
    EntityRef,
    Graph,
    IndexSpec,
    Registry,
)
import pickle

from src.common.pickle_store import LazyRegistryProxy, PickleStore, lazy_proxy_for


class _Person(Entity):
    kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
    name: str


class _PersonRegistry(Registry[_Person, str]):
    indexes = [IndexSpec(name="by_name", key_fn=lambda p: p.name)]

    def get_id(self, entity: _Person) -> str:
        return entity.id


def _people() -> _PersonRegistry:
    reg = _PersonRegistry()
    reg.add(_Person(id="alice", name="Alice"))
    reg.add(_Person(id="bob", name="Bob"))
    return reg


def test_graph_meta_defaults():
    g = Graph(project_id="p1")
    assert g.schema_version == 2
    assert g.project_id == "p1"
    assert g.built_at is not None
    assert g.registries == {}


def test_registry_for_and_resolve():
    g = Graph(project_id="p1", registries={EntityKind.GIT_ACCOUNT: _people()})

    reg = g.registry_for(EntityKind.GIT_ACCOUNT)
    assert reg is not None
    assert len(reg) == 2

    # Unknown kind -> None, not KeyError.
    assert g.registry_for(EntityKind.COMMIT) is None

    # resolve() routes through the right registry.
    alice = g.resolve(EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice"))
    assert alice is not None
    assert alice.name == "Alice"

    assert g.resolve(EntityRef(kind=EntityKind.GIT_ACCOUNT, id="ghost")) is None
    assert g.resolve(EntityRef(kind=EntityKind.COMMIT, id="x")) is None


def test_graph_rejects_non_registry_values():
    with pytest.raises(Exception):
        Graph(project_id="p", registries={EntityKind.GIT_ACCOUNT: "not a registry"})


def test_graph_accepts_lazy_proxy(tmp_path: Path):
    """A LazyRegistryProxy must be valid in Graph.registries values."""
    reg = _people()
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.GIT_ACCOUNT.value, reg)

    proxy = LazyRegistryProxy(
        store,
        EntityKind.GIT_ACCOUNT.value,
        lambda: store.read_registry(EntityKind.GIT_ACCOUNT.value, _PersonRegistry),
    )
    g = Graph(project_id="p", registries={EntityKind.GIT_ACCOUNT: proxy})
    assert proxy.is_loaded is False

    # Resolving through the graph triggers the lazy load transparently.
    alice = g.resolve(EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice"))
    assert alice is not None
    assert proxy.is_loaded is True


def test_graph_dump_writes_per_registry_files(tmp_path: Path):
    g = Graph(
        project_id="p1",
        registries={EntityKind.GIT_ACCOUNT: _people()},
    )
    store = PickleStore(tmp_path / "store")
    g.dump(store)

    # One pickle per registry + meta.json.
    assert store.path_for(EntityKind.GIT_ACCOUNT.value).exists()
    meta = store.meta_read()
    assert meta is not None
    assert meta["project_id"] == "p1"
    assert meta["schema_version"] == 2
    assert EntityKind.GIT_ACCOUNT.value in meta["registries"]


def test_graph_lazy_no_registries_yet_returns_empty():
    """Chunk 1 has no concrete registry bindings — ``lazy`` returns a
    well-formed Graph with empty ``registries``. Downstream chunks populate
    it."""
    g = Graph.lazy("p1", PickleStore("/tmp/does-not-exist"))
    assert g.project_id == "p1"
    assert g.registries == {}


# ---- Required-fix tests --------------------------------------------------


def test_graph_dump_with_proxy_writes_real_registry_payload(tmp_path: Path):
    """Required fix #2: ``Graph.dump`` must materialize proxies before
    pickling so on-disk files contain the real registry (not a proxy with
    an unpicklable loader).
    """
    # Pre-stage a registry on disk and wrap it in a proxy.
    seed_store = PickleStore(tmp_path / "seed")
    seed_store.write_registry(EntityKind.GIT_ACCOUNT.value, _people())

    proxy = lazy_proxy_for(
        _PersonRegistry,
        seed_store,
        EntityKind.GIT_ACCOUNT.value,
        lambda: seed_store.read_registry(
            EntityKind.GIT_ACCOUNT.value, _PersonRegistry
        ),
    )

    g = Graph(project_id="p1", registries={EntityKind.GIT_ACCOUNT: proxy})

    # Dump the graph to a fresh store. This MUST NOT raise even though
    # the proxy's loader is an unpicklable lambda.
    out_store = PickleStore(tmp_path / "out")
    g.dump(out_store)  # would raise PicklingError before the fix

    # The on-disk shape is the real per-registry payload (per §8.1).
    raw = out_store.read(EntityKind.GIT_ACCOUNT.value)
    assert raw is not None
    restored = pickle.loads(raw)
    assert isinstance(restored, _PersonRegistry)
    assert not isinstance(restored, LazyRegistryProxy)
    assert restored.ids() == {"alice", "bob"}


def test_graph_round_trip_with_named_typed_field(tmp_path: Path):
    """End-to-end Chunk-2 shape: a custom Graph subclass with a named
    typed registry field accepts a lazy proxy AND survives dump→reload."""
    from pydantic import ConfigDict

    class TypedGraph(Graph):
        # Chunk 2+ will declare each registry as a named field. This test
        # proves the kernel already supports that pattern.
        model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
        git_accounts: _PersonRegistry

    # Stage a registry, build a proxy, drop it into the typed field.
    seed_store = PickleStore(tmp_path / "seed")
    seed_store.write_registry(EntityKind.GIT_ACCOUNT.value, _people())

    proxy = lazy_proxy_for(
        _PersonRegistry,
        seed_store,
        EntityKind.GIT_ACCOUNT.value,
        lambda: seed_store.read_registry(
            EntityKind.GIT_ACCOUNT.value, _PersonRegistry
        ),
    )
    g = TypedGraph(project_id="p1", git_accounts=proxy)

    # isinstance contract on the typed field.
    assert isinstance(g.git_accounts, _PersonRegistry)
    assert proxy.is_loaded is False

    # Functional access works through the proxy.
    assert g.git_accounts.get("alice").name == "Alice"
    assert proxy.is_loaded is True


def test_graph_lazy_rejects_mismatched_schema_version(tmp_path: Path):
    """Optional fix #2: ``Graph.lazy`` enforces ``schema_version`` per §8.3."""
    store = PickleStore(tmp_path)
    store.meta_write({"schema_version": 999, "project_id": "p1"})
    with pytest.raises(ValueError, match="schema_version"):
        Graph.lazy("p1", store)


def test_graph_lazy_accepts_matching_schema_version(tmp_path: Path):
    store = PickleStore(tmp_path)
    store.meta_write({"schema_version": 2, "project_id": "p1"})
    g = Graph.lazy("p1", store)
    assert g.project_id == "p1"
    assert g.schema_version == 2
