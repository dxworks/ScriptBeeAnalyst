"""Per-registry pickle layout + lazy proxy.

Implements §8 of ``architectural_changes.md``:

  * One ``.pkl`` per registry, one ``meta.json`` per graph.
  * ``LazyRegistryProxy`` defers ``Registry.load(...)`` until first use AND
    presents itself to Pydantic as a real ``Registry`` subclass (so it can
    sit in typed fields like ``commits: CommitRegistry`` once Chunk 2+
    flips ``Graph.registries`` to named fields).

Layout under ``base_dir``::

    {base_dir}/
      meta.json
      projects.pkl
      git_accounts.pkl
      ...

``PickleStore`` is a thin filesystem wrapper. Cloud-storage backends
(Supabase Storage, S3, etc.) will subclass or wrap it later — the surface
is intentionally narrow.

Bypass / load semantics for ``LazyRegistryProxy``
-------------------------------------------------

The proxy's own private state (the store handle, the loader callable, the
cached loaded-registry slot) is always reachable without a load. Anything
else routes through ``_load()``.

In particular, these dunders are **load-free** (the proxy answers them
without materializing): ``__class__``, ``__init__``, ``__repr__``,
``__reduce__`` (delegates *after* a forced load — but materialize is the
explicit contract), plus Pydantic's ``model_config`` / ``model_fields``
introspection.

These dunders **do** force a load (they need to behave like the real
registry): ``__iter__``, ``__len__``, ``__contains__``, ``__getitem__``.
Anything else accessed via attribute (e.g. ``proxy.get(...)``,
``proxy.by_author[ref]``) forces a load too.

Other dunders not listed (``__bool__``, ``__eq__``, ``__hash__``,
``__copy__``, ``__deepcopy__``, ``__sizeof__``) fall through to the
default Python machinery acting on the proxy object itself, without
triggering a load. If you need them to reflect the inner registry, call
``proxy.materialize()`` first.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Type

from pydantic import PrivateAttr

from .kernel.registry import Registry

if TYPE_CHECKING:
    pass


class PickleStore:
    """Filesystem-backed store, one file per registry + a meta.json.

    ``registry_name`` is the stable on-disk name; in the v2 layout this is
    the ``EntityKind.value`` (e.g. ``"commit"``, ``"trait"``).
    """

    META_FILENAME = "meta.json"

    def __init__(self, base_dir: Path | str) -> None:
        self._base = Path(base_dir)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------
    @property
    def base_dir(self) -> Path:
        return self._base

    def path_for(self, registry_name: str) -> Path:
        if not registry_name or "/" in registry_name or registry_name.startswith("."):
            raise ValueError(f"Invalid registry name: {registry_name!r}")
        return self._base / f"{registry_name}.pkl"

    def meta_path(self) -> Path:
        return self._base / self.META_FILENAME

    # ------------------------------------------------------------------
    # Bytes-level IO (the primitive surface)
    # ------------------------------------------------------------------
    def read(self, registry_name: str) -> Optional[bytes]:
        """Return the raw pickle bytes for ``registry_name`` or ``None``."""
        path = self.path_for(registry_name)
        if not path.exists():
            return None
        return path.read_bytes()

    def write(self, registry_name: str, data: bytes) -> None:
        """Atomically write raw bytes for ``registry_name``."""
        path = self.path_for(registry_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def list_registries(self) -> list[str]:
        """Return all registry names present on disk, sorted."""
        if not self._base.exists():
            return []
        return sorted(p.stem for p in self._base.glob("*.pkl"))

    # ------------------------------------------------------------------
    # meta.json
    # ------------------------------------------------------------------
    def meta_read(self) -> Optional[dict[str, Any]]:
        path = self.meta_path()
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def meta_write(self, meta: dict[str, Any]) -> None:
        path = self.meta_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True))
        tmp.replace(path)

    # ------------------------------------------------------------------
    # Convenience helpers used by Registry / Graph.
    # ------------------------------------------------------------------
    def write_registry(self, registry_name: str, registry: "Registry[Any, Any]") -> None:
        """Pickle ``registry`` to ``registry_name.pkl``.

        If ``registry`` is a ``LazyRegistryProxy``, it is materialized first
        so the on-disk payload is the real registry (per §8.1), never a
        proxy wrapper carrying an unpicklable loader.
        """
        if isinstance(registry, LazyRegistryProxy):
            registry = registry.materialize()
        self.write(registry_name, pickle.dumps(registry, protocol=pickle.HIGHEST_PROTOCOL))

    def read_registry(
        self,
        registry_name: str,
        expected_cls: type["Registry[Any, Any]"],
    ) -> "Registry[Any, Any]":
        """Load and reindex a registry from disk. Raises if missing."""
        data = self.read(registry_name)
        if data is None:
            raise FileNotFoundError(self.path_for(registry_name))
        obj = pickle.loads(data)
        if not isinstance(obj, expected_cls):
            raise TypeError(
                f"Registry at {registry_name!r} is {type(obj).__name__}, "
                f"expected {expected_cls.__name__}"
            )
        obj.reindex()
        return obj


# ---------------------------------------------------------------------------
# LazyRegistryProxy
# ---------------------------------------------------------------------------
#
# Strategy: ``LazyRegistryProxy`` is a concrete ``Registry`` subclass.
# That means:
#
#   * ``isinstance(proxy, Registry)`` is True — fits ``Graph.registries:
#     dict[EntityKind, Any]`` with the existing isinstance-validator.
#   * For typed named fields (Chunk 2+: ``commits: CommitRegistry``), use
#     ``lazy_proxy_for(CommitRegistry, store, name, loader)`` which returns
#     an instance of a dynamically synthesized subclass
#     ``LazyCommitRegistry(LazyRegistryProxy, CommitRegistry)`` — that
#     instance passes ``isinstance(..., CommitRegistry)`` too.
#
# Implementation: the proxy's own ``_items`` / ``_indexes`` (inherited
# from ``Registry``) are kept empty and unused; every public method
# overridden here forwards to the lazily-loaded *inner* registry held in
# ``_loaded``.

# Names of "proxy private" attributes that must never trigger a load.
_PROXY_PRIVATE: frozenset[str] = frozenset(
    {"_store", "_registry_name", "_loader", "_loaded"}
)


class LazyRegistryProxy(Registry[Any, Any]):
    """Transparent stand-in for a ``Registry`` not yet loaded from disk.

    First access to any non-internal method/attribute triggers ``loader()``
    (which deserializes the underlying registry) and caches the result.
    Subsequent accesses go straight to the cached registry — no re-load.

    Construction (low-level)::

        proxy = LazyRegistryProxy(
            store=store,
            registry_name="commits",
            loader=lambda: CommitRegistry.load(store.path_for("commits")),
        )

    Recommended construction (typed-field-ready)::

        proxy = lazy_proxy_for(CommitRegistry, store, "commits", loader)
        # isinstance(proxy, CommitRegistry) is True

    See module docstring for bypass / load semantics of dunders.
    """

    # Allow Pydantic to accept arbitrary types in our private slots.
    model_config = {"arbitrary_types_allowed": True, "extra": "forbid"}

    _store: "PickleStore" = PrivateAttr()
    _registry_name: str = PrivateAttr()
    _loader: Callable[[], "Registry[Any, Any]"] = PrivateAttr()
    _loaded: Optional["Registry[Any, Any]"] = PrivateAttr(default=None)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(
        self,
        store: "PickleStore",
        registry_name: str,
        loader: Callable[[], "Registry[Any, Any]"],
    ) -> None:
        # BaseModel.__init__ initializes private attrs to their defaults
        # and triggers model_post_init.
        super().__init__()
        # Now stash proxy state via the standard PrivateAttr channel.
        self._store = store
        self._registry_name = registry_name
        self._loader = loader
        self._loaded = None

    def model_post_init(self, __context: Any) -> None:
        # Skip the Registry index-building in model_post_init — the proxy
        # never holds its own entities. Indexes come from the loaded inner
        # registry.
        if self.__pydantic_private__ is not None:
            self.__pydantic_private__["_indexes"] = {}

    # ------------------------------------------------------------------
    # Proxy core
    # ------------------------------------------------------------------
    @property
    def is_loaded(self) -> bool:
        return self._loaded is not None

    def _load(self) -> "Registry[Any, Any]":
        if self._loaded is None:
            self._loaded = self._loader()
        return self._loaded

    def materialize(self) -> "Registry[Any, Any]":
        """Force the underlying registry to load and return it.

        Use this when you need to hand the *real* registry to code that
        doesn't know about lazy proxies (e.g. pickling code).
        """
        return self._load()

    # ------------------------------------------------------------------
    # ``Registry`` surface — every method forwards through ``_load()``.
    # ------------------------------------------------------------------
    def get_id(self, entity: Any) -> Any:
        return self._load().get_id(entity)

    def get(self, id: Any) -> Any:
        return self._load().get(id)

    def add(self, entity: Any) -> Any:
        return self._load().add(entity)

    def remove(self, id: Any) -> Any:
        return self._load().remove(id)

    def all(self):  # noqa: D401 — matches Registry
        return self._load().all()

    def ids(self):
        return self._load().ids()

    def reindex(self) -> None:
        # Only reindex if we've actually materialized — a proxy with no
        # loaded inner registry has nothing to index.
        if self._loaded is not None:
            self._loaded.reindex()

    def index(self, name: str):
        return self._load().index(name)

    # ------------------------------------------------------------------
    # Dunders that must behave like the real registry → trigger a load.
    # ------------------------------------------------------------------
    def __iter__(self):  # type: ignore[override]
        return iter(self._load())

    def __len__(self) -> int:
        return len(self._load())

    def __contains__(self, item: object) -> bool:
        return item in self._load()

    def __getitem__(self, item: Any) -> Any:
        return self._load()[item]  # type: ignore[index]

    # ------------------------------------------------------------------
    # Attribute lookup: surface declared indexes (e.g. ``proxy.by_author``)
    # by forwarding to the loaded registry — and only after load.
    # ------------------------------------------------------------------
    def __getattr__(self, item: str) -> Any:
        # Proxy-private state must NEVER trigger a load. Let Pydantic /
        # PrivateAttr serve them via the BaseModel chain.
        if item in _PROXY_PRIVATE or item in ("_items", "_indexes"):
            return super().__getattr__(item)  # type: ignore[misc]
        # Dunders that arrive here are misses on the proxy class itself —
        # don't synthesize them by forcing a load.
        if item.startswith("__") and item.endswith("__"):
            raise AttributeError(item)
        # Anything else (e.g. ``by_author``) is a real registry attribute.
        return getattr(self._load(), item)

    # ------------------------------------------------------------------
    # Pickling: serialize the materialized inner registry, not ourselves.
    # ------------------------------------------------------------------
    def __reduce__(self) -> tuple[Any, ...]:
        # Pickling a proxy yields the inner Registry payload — the unpickled
        # value is a normal Registry instance (no proxy wrapper, no
        # unpicklable loader). This matches §8.1: one .pkl per registry,
        # the bytes ARE the registry.
        inner = self._load()
        return inner.__reduce__()

    def __repr__(self) -> str:
        if self.is_loaded:
            return (
                f"<LazyRegistryProxy({self._registry_name!r}, "
                f"loaded={type(self._loaded).__name__})>"
            )
        return f"<LazyRegistryProxy({self._registry_name!r}, unloaded)>"


# ---------------------------------------------------------------------------
# Factory: typed proxy for a concrete registry class.
# ---------------------------------------------------------------------------

# Cache so repeated calls for the same registry class return the same
# synthesized subclass (otherwise every call would create a new type, which
# matters for isinstance identity and repr stability).
_PROXY_CLASS_CACHE: dict[type, type] = {}


def lazy_proxy_for(
    registry_cls: Type["Registry[Any, Any]"],
    store: "PickleStore",
    registry_name: str,
    loader: Callable[[], "Registry[Any, Any]"],
) -> "LazyRegistryProxy":
    """Return a lazy proxy that ``isinstance``-matches ``registry_cls``.

    Synthesizes (and caches) a subclass
    ``Lazy<RegistryClsName>(LazyRegistryProxy, registry_cls)`` so the
    resulting instance is **both** a ``LazyRegistryProxy`` AND a
    ``registry_cls``. This is the pattern Chunk 2+ should use to populate
    typed ``Graph`` fields lazily::

        graph = Graph(
            project_id="p1",
            registries={
                EntityKind.COMMIT: lazy_proxy_for(
                    CommitRegistry, store, "commit",
                    lambda: store.read_registry("commit", CommitRegistry),
                ),
            },
        )
        isinstance(graph.registries[EntityKind.COMMIT], CommitRegistry)  # True

    Or, when ``Graph.registries`` is replaced with named typed fields::

        class Graph(BaseModel):
            commits: CommitRegistry
            ...

        Graph(commits=lazy_proxy_for(CommitRegistry, store, "commit", loader),
              project_id="p1")
    """
    if registry_cls is LazyRegistryProxy or registry_cls is Registry:
        # Plain proxy without a concrete-class shim.
        return LazyRegistryProxy(store=store, registry_name=registry_name, loader=loader)

    cls = _PROXY_CLASS_CACHE.get(registry_cls)
    if cls is None:
        cls = type(
            f"Lazy{registry_cls.__name__}",
            (LazyRegistryProxy, registry_cls),
            {"__module__": LazyRegistryProxy.__module__},
        )
        _PROXY_CLASS_CACHE[registry_cls] = cls
    return cls(store=store, registry_name=registry_name, loader=loader)  # type: ignore[return-value]


__all__ = ["PickleStore", "LazyRegistryProxy", "lazy_proxy_for"]
