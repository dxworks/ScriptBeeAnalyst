from difflib import Differ
from typing import List, Tuple

from src.inspector_git.reader.dto.gitlog.hunk_dto import HunkDTO
from src.inspector_git.reader.dto.gitlog.hunk_type import HunkType
from src.inspector_git.reader.dto.gitlog.line_chnage_dto import LineChangeDTO
from src.inspector_git.reader.dto.iglog.content_meta import ContentMeta
from src.inspector_git.reader.dto.iglog.hunk_change_meta import HunkChangeMeta
from src.inspector_git.reader.extractors.meta_extractor import MetaExtractor

class HunkChangeMetaExtractor(MetaExtractor[HunkChangeMeta]):
    def __init__(self):
        self.content_meta_splitter = "-"
        self.hunk_meta_splitter = " "

    @property
    def line_prefix(self) -> str:
        return "~>"

    def extract(self, hunk_dto: HunkDTO) -> str:
        if hunk_dto.type == HunkType.MODIFY:
            add_content_meta, delete_content_meta = self._diff_content_meta(hunk_dto)
        else:
            add_content_meta, delete_content_meta = self._get_add_and_delete_content_meta(hunk_dto)

        unmodified_content_meta = self._get_unmodified_content_meta(
            delete_content_meta, add_content_meta, hunk_dto
        )

        return self._get_formatted_line(
            HunkChangeMeta(
                added_content_meta=add_content_meta,
                deleted_content_meta=delete_content_meta,
                unmodified_content_meta=unmodified_content_meta,
            )
        )

    def _diff_content_meta(self, hunk_dto: HunkDTO) -> Tuple[ContentMeta, ContentMeta]:
        deleted_chars = self._get_text_as_list(hunk_dto.deleted_line_changes)
        added_chars = self._get_text_as_list(hunk_dto.added_line_changes)

        if not deleted_chars and not added_chars:
            return ContentMeta(0, 0), ContentMeta(0, 0)

        differ = Differ()
        diff = list(differ.compare(deleted_chars, added_chars))
        added_meta = ContentMeta(0, 0)
        deleted_meta = ContentMeta(0, 0)

        for d in diff:
            if d.startswith("+ "):
                added_meta += ContentMeta(1, 1 if d[2].isspace() else 0)
            elif d.startswith("- "):
                deleted_meta += ContentMeta(1, 1 if d[2].isspace() else 0)

        return added_meta, deleted_meta

    def _get_add_and_delete_content_meta(self, hunk_dto: HunkDTO) -> Tuple[ContentMeta, ContentMeta]:
        return (
            self._get_content_meta_from_line_changes(hunk_dto.added_line_changes),
            self._get_content_meta_from_line_changes(hunk_dto.deleted_line_changes),
        )

    def _get_content_meta_from_line_changes(self, line_changes: List[LineChangeDTO]) -> ContentMeta:
        return self._get_content_meta(self._get_text_as_list(line_changes))

    def _get_unmodified_content_meta(
        self, delete_content_meta: ContentMeta, add_content_meta: ContentMeta, hunk_dto: HunkDTO
    ) -> ContentMeta:
        if delete_content_meta.is_empty() or add_content_meta.is_empty():
            return ContentMeta(0, 0)
        old_content_meta = self._get_content_meta(
            self._get_text_as_list(hunk_dto.deleted_line_changes)
        )
        return old_content_meta - delete_content_meta

    def _get_content_meta(self, chars: List[str]) -> ContentMeta:
        return ContentMeta(len(chars), sum(1 for c in chars if c.isspace()))

    def _get_text_as_list(self, line_changes: List[LineChangeDTO]) -> List[str]:
        return list("".join(lc.content or "" for lc in line_changes))

    def _get_formatted_line(self, hunk_change_meta: HunkChangeMeta) -> str:
        return (
            f"{self._get_formatted_content_meta(hunk_change_meta.added_content_meta)}"
            f"{self.hunk_meta_splitter}"
            f"{self._get_formatted_content_meta(hunk_change_meta.deleted_content_meta)}"
            f"{self.hunk_meta_splitter}"
            f"{self._get_formatted_content_meta(hunk_change_meta.unmodified_content_meta)}"
        )

    def _get_formatted_content_meta(self, content_meta: ContentMeta) -> str:
        return f"{content_meta.total_chars}{self.content_meta_splitter}{content_meta.spaces}"

    def parse(self, line: str) -> HunkChangeMeta:
        change_meta_strings = line.split(self.hunk_meta_splitter)
        return HunkChangeMeta(
            added_content_meta=self._get_content_meta_from_string(change_meta_strings[0]),
            deleted_content_meta=self._get_content_meta_from_string(change_meta_strings[1]),
            unmodified_content_meta=self._get_content_meta_from_string(change_meta_strings[2]),
        )

    def _get_content_meta_from_string(self, content_meta_string: str) -> ContentMeta:
        fields = content_meta_string.split(self.content_meta_splitter)
        return ContentMeta(int(fields[0]), int(fields[1]))
