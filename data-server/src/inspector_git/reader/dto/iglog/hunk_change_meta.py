from dataclasses import dataclass
from src.inspector_git.reader.dto.iglog.content_meta import ContentMeta


@dataclass(frozen=True)
class HunkChangeMeta:

    added_content_meta: ContentMeta
    deleted_content_meta: ContentMeta
    unmodified_content_meta: ContentMeta

    def total_chars_delta(self) -> int:
        """
        Exemplu de metodă utilă: diferența netă de caractere
        (adăugat - șters). Nu exista în Kotlin original dar e un helper
        mic, opțional — îl păstrez pentru utilitate practică.
        """
        return self.added_content_meta.total_chars - self.deleted_content_meta.total_chars

    def is_empty(self) -> bool:
        """
        Returnează True dacă toate ContentMeta sunt goale.
        """
        return (
            self.added_content_meta.is_empty()
            and self.deleted_content_meta.is_empty()
            and self.unmodified_content_meta.is_empty()
        )
