from dataclasses import dataclass
from typing import Optional


@dataclass
class AnnotatedLineDTO:
    """
    DTO ce reprezintă o linie dintr-un fișier cu informații de commit.
    """
    commit_id: str
    number: int
    content: Optional[str] = None

    def __str__(self) -> str:
        return f"{self.commit_id} {self.number}) {self.content}"
