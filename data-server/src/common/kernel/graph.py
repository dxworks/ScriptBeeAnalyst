"""Graph root for the v2 model.

Chunk 1 deliberately keeps ``Graph`` minimal: meta fields, a kind-keyed
registry map, and the method signatures every downstream chunk depends on.
Later chunks (one per domain) replace the dict with named typed fields per
§1.6 of the architectural plan, e.g.::

    class Graph(BaseModel):
        ...
        commits: CommitRegistry
        files:   FileRegistry
        ...

The signatures below are stable across that migration: ``registry_for`` /
``resolve`` / ``lazy`` / ``dump`` keep working when fields become explicit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .entity import Entity
from .kinds import EntityKind
from .ref import EntityRef
from .registry import Registry

if TYPE_CHECKING:
    from ..pickle_store import PickleStore


#: Current schema version written by ``Graph.dump`` and required by
#: ``Graph.lazy``. Bumping = re-import (greenfield migration, no readers
#: kept for old majors — see §8.3 of the plan).
SCHEMA_VERSION: int = 2


class Graph(BaseModel):
    """Root of the v2 graph.

    Chunk 1 surface only. Downstream chunks will add named registry fields
    (one per domain) — see TODO below.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    # --- meta ---
    schema_version: int = SCHEMA_VERSION
    project_id: str
    built_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # --- registries ---
    # TODO(chunk 2+): replace this generic mapping with named per-domain
    # registry fields, e.g. ``commits: CommitRegistry``. The accessor methods
    # below (``registry_for`` / ``resolve``) absorb that change without
    # touching callers.
    #
    # NOTE: we type the *values* as ``Any`` rather than ``Registry[Any, Any]``
    # because Pydantic v2 cannot validate an abstract generic base
    # (``Registry`` declares ``@abstractmethod get_id``). The
    # ``_validate_registries`` validator below enforces the type at runtime.
    registries: dict[EntityKind, Any] = Field(default_factory=dict)

    @field_validator("registries", mode="after")
    @classmethod
    def _validate_registries(
        cls, value: dict[EntityKind, Any]
    ) -> dict[EntityKind, Any]:
        # Late import to avoid a circular dep with pickle_store at module load.
        from ..pickle_store import LazyRegistryProxy

        for kind, reg in value.items():
            if not isinstance(reg, (Registry, LazyRegistryProxy)):
                raise TypeError(
                    f"Graph.registries[{kind!r}] must be a Registry or "
                    f"LazyRegistryProxy, got {type(reg).__name__}"
                )
        return value

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------
    def registry_for(self, kind: EntityKind) -> Optional[Registry[Any, Any]]:
        """Return the registry that owns ``kind``, or ``None`` if not bound."""
        return self.registries.get(kind)

    def resolve(self, ref: EntityRef) -> Optional[Entity]:
        """Resolve an ``EntityRef`` through the right registry. O(1)."""
        registry = self.registry_for(ref.kind)
        if registry is None:
            return None
        return registry.get(ref.id)

    # ------------------------------------------------------------------
    # IO (signatures only in Chunk 1; full implementation lands with
    # ``PickleStore`` integration in later chunks.)
    # ------------------------------------------------------------------
    @classmethod
    def lazy(
        cls,
        project_id: str,
        store: "PickleStore",
    ) -> "Graph":
        """Build a ``Graph`` whose registry fields are lazy proxies.

        Each registry is loaded from ``store`` on first access. In Chunk 1
        this is a thin wrapper around ``LazyRegistryProxy`` — see
        ``common/pickle_store.py``. Concrete registry classes are bound by
        later chunks; this signature is reserved here for them.
        """
        # Local import to avoid a kernel ↔ pickle_store cycle.
        from ..pickle_store import LazyRegistryProxy

        meta = store.meta_read() or {}

        # Enforce schema_version compatibility on the major version. We
        # treat ``schema_version`` as a single integer (no minor/patch
        # split today); any mismatch is a hard error — there are no
        # backward-compat readers per §8.3.
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
        registries: dict[EntityKind, Registry[Any, Any]] = {}
        # Chunk 1 has no concrete registry classes to bind. Once Chunk 2+
        # adds named fields, this method instantiates a ``LazyRegistryProxy``
        # per kind. The proxy machinery is already in place — see
        # ``LazyRegistryProxy`` and ``lazy_proxy_for`` for usage / tests.
        _ = LazyRegistryProxy  # keep the import live for downstream chunks
        return cls(
            project_id=project_id,
            built_at=built_at,
            registries=registries,
        )

    def dump(self, store: "PickleStore") -> None:
        """Persist every bound registry to ``store``.

        Each registry is written to ``<registry_name>.pkl`` (and metadata
        to ``meta.json``). Registry names are the ``EntityKind`` values
        (a stable string), which matches the per-file layout in §8.1.
        """
        for kind, registry in self.registries.items():
            store.write_registry(kind.value, registry)
        store.meta_write(
            {
                "schema_version": self.schema_version,
                "project_id": self.project_id,
                "built_at": self.built_at.isoformat(),
                "registries": sorted(k.value for k in self.registries),
            }
        )


__all__ = ["Graph"]
