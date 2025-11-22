from src.common.loader import BaseJsonLoader
from src.github_miner import JsonFileFormatGithub


class GithubJsonLoader(BaseJsonLoader):
    def load(self) -> JsonFileFormatGithub:
        data = self._read_json()
        return JsonFileFormatGithub.model_validate(data)
