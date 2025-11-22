from abc import ABC, abstractmethod
from typing import Generic, TypeVar
from src.inspector_git.reader.dto.gitlog.hunk_dto import HunkDTO

T = TypeVar("T")


class MetaExtractor(ABC, Generic[T]):
    @property
    @abstractmethod
    def line_prefix(self) -> str:
        """Prefix for lines handled by this extractor."""
        pass

    def write(self, hunk_dto: HunkDTO) -> str:
        return f"{self.line_prefix}{self.extract(hunk_dto)}"

    @abstractmethod
    def extract(self, hunk_dto: HunkDTO) -> str:
        """Extract a string representation from a HunkDTO."""
        pass

    def read(self, line: str) -> T:
        return self.parse(line.removeprefix(self.line_prefix))

    @abstractmethod
    def parse(self, line: str) -> T:
        """Parse a string into the target type T."""
        pass
