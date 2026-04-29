from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field


EntityKind = Literal["file", "commit", "author", "issue", "pr", "component", "project"]
WindowKind = Literal["lifetime", "recent"]
HighlightKind = Literal["none", "good", "warn", "bad"]

CellValue = Union[float, int, str, None]


class Classifier(BaseModel):
    """Mandatory 1:1 attribute on an entity (e.g. commit.message.nature='bugfix')."""
    name: str
    value: str


class Trait(BaseModel):
    """Optional 0..N concern on an entity (e.g. anomaly.knowledge.Orphan).

    Mirrors dx's `Anomaly` — `severity` maps to `Anomaly.severity().value`,
    `evidence` carries the proxy/threshold context so the AI agent can caveat.
    """
    name: str
    family: str
    severity: float = 1.0
    evidence: dict[str, Any] = Field(default_factory=dict)


class EntityTags(BaseModel):
    """Classifiers + traits attached to one entity."""
    entity_kind: EntityKind
    entity_id: str
    classifiers: dict[str, str] = Field(default_factory=dict)
    traits: list[Trait] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.entity_kind}:{self.entity_id}"


class Relation(BaseModel):
    source_kind: EntityKind
    source_id: str
    target_kind: EntityKind
    target_id: str
    kind: str
    strength: float
    extras: dict[str, Any] = Field(default_factory=dict)


class RelationFile(BaseModel):
    """A bag of relations of the same kind/window — CSV-ready."""
    kind: str
    window: WindowKind = "lifetime"
    relations: list[Relation] = Field(default_factory=list)


class OverviewCell(BaseModel):
    """Mirrors dx's `lifetime | recent | trend%` triple per column."""
    lifetime_value: CellValue = None
    recent_value: CellValue = None
    trend_percent: Optional[float] = None
    highlight: HighlightKind = "none"


class OverviewRow(BaseModel):
    entity_id: str
    cells: dict[str, OverviewCell] = Field(default_factory=dict)


class OverviewTable(BaseModel):
    name: str
    entity_kind: EntityKind
    columns: list[str]
    rows: list[OverviewRow] = Field(default_factory=list)


class Component(BaseModel):
    """Folder-based component (dx-parity — path-prefix grouping)."""
    name: str
    path_prefix: str
    file_paths: list[str] = Field(default_factory=list)


class Enrichments(BaseModel):
    """Top-level container stored at `graph_data['enrichments']`."""
    generated_at: datetime
    recent_window_days: int
    components: list[Component] = Field(default_factory=list)
    tags_by_entity: dict[str, EntityTags] = Field(default_factory=dict)
    relations: list[RelationFile] = Field(default_factory=list)
    overviews: list[OverviewTable] = Field(default_factory=list)

    def find_tags(self, entity_kind: EntityKind, entity_id: str) -> Optional[EntityTags]:
        return self.tags_by_entity.get(f"{entity_kind}:{entity_id}")

    def entities_with_trait(self, trait_name: str) -> list[EntityTags]:
        return [
            t for t in self.tags_by_entity.values()
            if any(tr.name == trait_name for tr in t.traits)
        ]

    def entities_with_classifier(
        self, entity_kind: EntityKind, classifier: str, value: str
    ) -> list[EntityTags]:
        return [
            t for t in self.tags_by_entity.values()
            if t.entity_kind == entity_kind and t.classifiers.get(classifier) == value
        ]

    def relation_file(self, kind: str, window: WindowKind = "lifetime") -> Optional[RelationFile]:
        return next(
            (r for r in self.relations if r.kind == kind and r.window == window),
            None,
        )

    def overview(self, name: str) -> Optional[OverviewTable]:
        return next((o for o in self.overviews if o.name == name), None)
