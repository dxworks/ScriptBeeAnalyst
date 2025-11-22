from src.common.loader import BaseJsonLoader
from src.jira_miner.reader_dto.models import JsonFileFormatJira


class JiraJsonLoader(BaseJsonLoader):
    def load(self) -> JsonFileFormatJira:
        data = self._read_json()
        return JsonFileFormatJira.model_validate(data)