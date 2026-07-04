from src.common.people.unified import UnifiedUser
from src.smart_merge.engine import AuthorSmartMergeEngine
from src.smart_merge.identity import SourceIdentity
from src.smart_merge.identity_extractor import extract_all_identities
from src.smart_merge.types import Suggestion

__all__ = [
    "AuthorSmartMergeEngine",
    "SourceIdentity",
    "Suggestion",
    "UnifiedUser",
    "extract_all_identities",
]
