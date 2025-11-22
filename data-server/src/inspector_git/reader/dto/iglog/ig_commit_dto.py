# /src/inspector_git/dto/iglog/ig_commit_dto.py

from dataclasses import dataclass
from typing import List

from src.inspector_git.reader.dto.commit_info_dto import CommitInfoDTO
from src.inspector_git.reader.dto.iglog.ig_change_dto import IgChangeDTO


@dataclass
class IgCommitDTO(CommitInfoDTO):
    changes: List[IgChangeDTO]
