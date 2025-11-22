from dataclasses import dataclass

@dataclass
class ContentMeta:
    total_chars: int
    spaces: int

    def __add__(self, other: "ContentMeta") -> "ContentMeta":
        if not isinstance(other, ContentMeta):
            return NotImplemented
        return ContentMeta(self.total_chars + other.total_chars,
                           self.spaces + other.spaces)

    def __sub__(self, other: "ContentMeta") -> "ContentMeta":
        if not isinstance(other, ContentMeta):
            return NotImplemented
        return ContentMeta(self.total_chars - other.total_chars,
                           self.spaces - other.spaces)

    def is_empty(self) -> bool:
        """Returnează True dacă total_chars este 0."""
        return self.total_chars == 0

    def __repr__(self) -> str:
        return f"ContentMeta(total_chars={self.total_chars}, spaces={self.spaces})"
