from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from src.smart_merge.identity import SourceIdentity


class SimilarityType(str, Enum):
    DIFFERENT = "DIFFERENT"
    SIMILAR = "SIMILAR"
    IDENTICAL = "IDENTICAL"
    SAME_AUTHOR = "SAME_AUTHOR"


@dataclass(frozen=True)
class Similarity:
    type: SimilarityType
    strength: int


@dataclass(frozen=True)
class Edge:
    a: str
    b: str
    type: SimilarityType
    strength: int


@dataclass
class SimilaritiesGraph:
    nodes: Dict[str, SourceIdentity] = field(default_factory=dict)
    adj: Dict[str, Dict[str, Edge]] = field(default_factory=dict)


MAX_IDENTITIES_PER_SUGGESTION = 50


@dataclass
class Suggestion:
    suggestion_id: str
    default_name: str
    default_email: str
    confidence: float
    identities: List[SourceIdentity]

    def to_dict(self, activity_counts: Optional[Dict[str, int]] = None) -> dict:
        """Serialize to a UI-safe dict.

        Truncates `identities` to MAX_IDENTITIES_PER_SUGGESTION (sorted by
        activity desc so the visible ones are the most important), and includes
        the real `total_identities` so the UI can offer pagination.
        """
        counts = activity_counts or {}
        ordered = sorted(
            self.identities, key=lambda i: counts.get(i.key, 0), reverse=True
        )
        truncated = ordered[:MAX_IDENTITIES_PER_SUGGESTION]
        return {
            "suggestion_id": self.suggestion_id,
            "default_name": self.default_name,
            "default_email": self.default_email,
            "confidence": round(self.confidence, 2),
            "total_identities": len(self.identities),
            "identities": [
                {
                    "source": i.source,
                    "source_key": i.source_key,
                    "name": i.name,
                    "email": i.email,
                    "login": i.login,
                }
                for i in truncated
            ],
        }


@dataclass(frozen=True)
class RejectedPair:
    """A pair of identities whose merge was rejected by the user."""
    project_id: str
    first_source: str
    first_source_key: str
    second_source: str
    second_source_key: str


@dataclass
class UserMapping:
    """Persisted mapping: a unified user and its constituent identities."""
    unified_user_id: str
    display_name: str
    primary_email: Optional[str]
    identities: List[SourceIdentity]
