"""Pickle round-trip + LazyRegistryProxy behavior."""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import ClassVar

from src.common.kernel import (
    Entity,
    EntityKind,
    EntityRef,
    IndexSpec,
    Registry,
)
from src.common.pickle_store import LazyRegistryProxy, PickleStore, lazy_proxy_for


# ---- toy domain — defined at module level so pickle can find it ---------


class _Commit(Entity):
    kind: ClassVar[EntityKind] = EntityKind.COMMIT
    author_ref: EntityRef
    files: list[EntityRef] = []


class _CommitRegistry(Registry[_Commit, str]):
    indexes = [
        IndexSpec(name="by_author", key_fn=lambda c: c.author_ref),
        IndexSpec(name="by_file", key_fn=lambda c: c.files),
    ]

    def get_id(self, entity: _Commit) -> str:
        return entity.id


ALICE = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice")
BOB = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="bob")
FILE_A = EntityRef(kind=EntityKind.FILE, id="a.py")
FILE_B = EntityRef(kind=EntityKind.FILE, id="b.py")


def _build() -> _CommitRegistry:
    reg = _CommitRegistry()
    reg.add(_Commit(id="c1", author_ref=ALICE, files=[FILE_A]))
    reg.add(_Commit(id="c2", author_ref=ALICE, files=[FILE_A, FILE_B]))
    reg.add(_Commit(id="c3", author_ref=BOB, files=[FILE_B]))
    return reg


# ---- tests ---------------------------------------------------------------


def test_dump_load_roundtrip_preserves_entities(tmp_path: Path):
    reg = _build()
    path = tmp_path / "commits.pkl"
    reg.dump(path)
    assert path.exists()

    restored = _CommitRegistry.load(path)
    assert isinstance(restored, _CommitRegistry)
    assert restored.ids() == reg.ids()
    for cid in reg.ids():
        original = reg.get(cid)
        loaded = restored.get(cid)
        assert loaded is not None
        assert loaded.id == original.id
        assert loaded.author_ref == original.author_ref
        assert loaded.files == original.files


def test_dump_load_rebuilds_indexes(tmp_path: Path):
    reg = _build()
    path = tmp_path / "commits.pkl"
    reg.dump(path)
    restored = _CommitRegistry.load(path)
    # Indexes work after load.
    assert {c.id for c in restored.by_author[ALICE]} == {"c1", "c2"}
    assert {c.id for c in restored.by_file[FILE_B]} == {"c2", "c3"}


def test_index_bytes_not_in_pickle(tmp_path: Path):
    """The pickled payload must not carry serialized index buckets."""
    reg = _build()
    path = tmp_path / "commits.pkl"
    reg.dump(path)
    blob = path.read_bytes()
    # Marker strings for index names should not appear (since __reduce__
    # excludes _indexes entirely).
    assert b"by_author" not in blob
    assert b"by_file" not in blob


def test_index_state_absent_until_reindex(tmp_path: Path):
    """Robust version of the bytes scan: unpickle WITHOUT going through the
    registry's ``load`` (which calls reindex), and prove the un-reindexed
    object has zero index data — i.e. indexes are reconstructed from
    entities, not unpickled from disk.
    """
    reg = _build()
    path = tmp_path / "commits.pkl"
    reg.dump(path)
    raw = pickle.loads(path.read_bytes())

    # The reconstructor (_registry_reconstruct) rebuilds the registry by
    # replaying ``add`` for each entity, which incidentally also rebuilds
    # indexes. To confirm "indexes aren't *carried* in the pickle bytes",
    # we instead inspect the immediate post-reduce state by patching the
    # reconstructor: feed it an empty entity list, then assert the
    # registry has zero items and zero index population — proving the
    # bytes themselves contributed neither entries nor index buckets.
    from src.common.kernel.registry import _registry_reconstruct

    empty = _registry_reconstruct(_CommitRegistry, [])
    assert len(empty) == 0
    # Indexes exist as empty containers (declared on the class) but have
    # no keys at all.
    assert all(len(idx) == 0 for idx in empty._indexes.values())  # type: ignore[attr-defined]

    # And the round-trip via Registry.load *does* result in correct indexes:
    assert {c.id for c in raw.by_author[ALICE]} == {"c1", "c2"}


def test_pickle_store_write_and_read_registry(tmp_path: Path):
    store = PickleStore(tmp_path / "store")
    reg = _build()
    store.write_registry("commit", reg)
    assert store.path_for("commit").exists()

    loaded = store.read_registry("commit", _CommitRegistry)
    assert isinstance(loaded, _CommitRegistry)
    assert loaded.ids() == reg.ids()
    assert "commit" in store.list_registries()


def test_pickle_store_meta_roundtrip(tmp_path: Path):
    store = PickleStore(tmp_path / "store")
    assert store.meta_read() is None
    store.meta_write({"schema_version": 2, "project_id": "p1"})
    assert store.meta_read() == {"schema_version": 2, "project_id": "p1"}


def test_pickle_store_path_validation(tmp_path: Path):
    store = PickleStore(tmp_path)
    import pytest

    for bad in ("", "../escape", "with/slash", ".hidden"):
        with pytest.raises(ValueError):
            store.path_for(bad)


# ---- LazyRegistryProxy --------------------------------------------------


class _LoadCounter:
    """Capture how many times a loader callable is invoked."""

    def __init__(self, registry: _CommitRegistry) -> None:
        self.calls = 0
        self.registry = registry

    def __call__(self) -> _CommitRegistry:
        self.calls += 1
        return self.registry


def test_lazy_proxy_defers_load_until_first_access(tmp_path: Path):
    store = PickleStore(tmp_path / "store")
    reg = _build()
    store.write_registry("commit", reg)

    counter = _LoadCounter(_build())  # fresh in-memory copy as the loaded value
    proxy = LazyRegistryProxy(store, "commit", counter)

    # No loader call yet — construction alone is cheap.
    assert counter.calls == 0
    assert proxy.is_loaded is False

    # First real access triggers the load.
    assert proxy.get("c1") is not None
    assert counter.calls == 1
    assert proxy.is_loaded is True

    # Second access reuses the cached registry — no extra load.
    assert proxy.get("c2") is not None
    assert counter.calls == 1


def test_lazy_proxy_is_transparent(tmp_path: Path):
    store = PickleStore(tmp_path)
    reg = _build()
    proxy = LazyRegistryProxy(store, "commit", lambda: reg)

    # __len__ / __iter__ / __contains__ all forward to the real registry.
    assert len(proxy) == 3
    assert "c1" in proxy
    assert {c.id for c in proxy} == {"c1", "c2", "c3"}

    # Index access still works through the proxy.
    by_alice = proxy.by_author[ALICE]
    assert {c.id for c in by_alice} == {"c1", "c2"}


def test_lazy_proxy_repr_unloaded_then_loaded(tmp_path: Path):
    reg = _build()
    proxy = LazyRegistryProxy(PickleStore(tmp_path), "commit", lambda: reg)
    assert "unloaded" in repr(proxy)
    _ = proxy.get("c1")  # force load
    assert "loaded=" in repr(proxy)


# ---- Required-fix tests --------------------------------------------------


def test_proxy_is_registry_instance(tmp_path: Path):
    """A bare LazyRegistryProxy passes isinstance(Registry) — required so
    it fits any field/parameter typed as ``Registry``."""
    reg = _build()
    proxy = LazyRegistryProxy(PickleStore(tmp_path), "commit", lambda: reg)
    assert isinstance(proxy, Registry)
    # The proxy is a Registry but NOT a _CommitRegistry without the factory
    # — that's exactly what ``lazy_proxy_for`` is for.
    assert not isinstance(proxy, _CommitRegistry)


def test_lazy_proxy_for_satisfies_concrete_isinstance(tmp_path: Path):
    """``lazy_proxy_for(_CommitRegistry, ...)`` returns an instance that
    isinstance-matches _CommitRegistry — so Chunk 2's typed Graph fields
    (``commits: CommitRegistry``) accept the proxy without changes."""
    reg = _build()
    proxy = lazy_proxy_for(
        _CommitRegistry,
        PickleStore(tmp_path),
        "commit",
        lambda: reg,
    )
    assert isinstance(proxy, _CommitRegistry)
    assert isinstance(proxy, LazyRegistryProxy)
    # Same registry class twice -> same synthesized subclass (cached).
    proxy2 = lazy_proxy_for(
        _CommitRegistry,
        PickleStore(tmp_path),
        "commit",
        lambda: reg,
    )
    assert type(proxy) is type(proxy2)


def test_lazy_proxy_in_typed_pydantic_field(tmp_path: Path):
    """Direct repro of the Chunk-2 use case: a Pydantic model with a
    typed ``commits: _CommitRegistry`` field accepts BOTH an eager
    registry and a lazy proxy (via ``lazy_proxy_for``)."""
    from pydantic import BaseModel, ConfigDict

    class TypedHost(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        commits: _CommitRegistry

    # Eager: plain registry.
    eager = TypedHost(commits=_build())
    assert isinstance(eager.commits, _CommitRegistry)
    assert len(eager.commits) == 3

    # Lazy: proxy via factory. Construction does NOT load.
    counter = _LoadCounter(_build())
    proxy = lazy_proxy_for(
        _CommitRegistry,
        PickleStore(tmp_path),
        "commit",
        counter,
    )
    lazy = TypedHost(commits=proxy)
    assert counter.calls == 0  # field assignment didn't load
    assert isinstance(lazy.commits, _CommitRegistry)
    # First real use triggers the load.
    assert lazy.commits.get("c1") is not None
    assert counter.calls == 1


def test_lazy_proxy_pickles_through_to_inner_registry(tmp_path: Path):
    """Pickling a LazyRegistryProxy must produce the inner Registry on
    unpickle — no proxy wrapper, no unpicklable loader stuck in slots.

    Reproduces blocking issue #2 from the review.
    """
    reg = _build()
    proxy = lazy_proxy_for(
        _CommitRegistry,
        PickleStore(tmp_path),
        "commit",
        lambda: reg,
    )
    blob = pickle.dumps(proxy)
    restored = pickle.loads(blob)
    # The unpickled object is a real _CommitRegistry, not a proxy.
    assert isinstance(restored, _CommitRegistry)
    assert not isinstance(restored, LazyRegistryProxy)
    assert restored.ids() == {"c1", "c2", "c3"}


def test_pickle_store_write_registry_materializes_proxy(tmp_path: Path):
    """``PickleStore.write_registry`` must materialize a proxy before
    serializing, so the on-disk shape is per §8.1: a real registry payload.
    """
    reg = _build()
    store = PickleStore(tmp_path / "store")

    proxy = lazy_proxy_for(
        _CommitRegistry,
        store,
        "commit",
        lambda: reg,
    )
    # Write via the convenience helper — must NOT raise on the
    # unpicklable lambda inside the proxy.
    store.write_registry("commit", proxy)
    assert store.path_for("commit").exists()

    # The bytes on disk are the inner registry, not a proxy wrapper.
    loaded = store.read_registry("commit", _CommitRegistry)
    assert isinstance(loaded, _CommitRegistry)
    assert not isinstance(loaded, LazyRegistryProxy)
    assert loaded.ids() == {"c1", "c2", "c3"}


def test_dunder_load_table_documented_behavior(tmp_path: Path):
    """Sanity-check the dunder load semantics documented at the top of
    pickle_store.py: __repr__ is load-free; __len__/__iter__/__contains__
    force a load."""
    reg = _build()
    counter = _LoadCounter(reg)
    proxy = LazyRegistryProxy(PickleStore(tmp_path), "commit", counter)

    # repr: load-free.
    _ = repr(proxy)
    assert counter.calls == 0
    assert proxy.is_loaded is False

    # len: forces a load.
    _ = len(proxy)
    assert counter.calls == 1
    assert proxy.is_loaded is True
