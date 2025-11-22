from dataclasses import dataclass

from src.inspector_git.reader.enums.chnage_type import ChangeType


@dataclass
class ChangeInfoDTO:
    old_file_name: str
    new_file_name: str
    type: ChangeType
    parent_commit_id: str
    is_binary: bool  # mapat de la @JsonProperty("b")
