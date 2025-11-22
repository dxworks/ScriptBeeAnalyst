import logging

from src.inspector_git.reader.git_client import GitClient

logger = logging.getLogger(__name__)

class GitLogPager:
    """
    Python equivalent of Kotlin's GitLogPager.
    Handles paginated retrieval of git logs using a GitClient instance.
    """

    def __init__(self, git_client: GitClient, page_size: int = 2000):
        self.git_client = git_client
        self.page_size = page_size

        # Initialize commit count and page count
        self._commit_count = self.git_client.get_commit_count()
        self._page_count = self._commit_count // self.page_size + 1
        self._counter = 0

        # Call to GitClient's rename limit setter
        self.git_client.set_rename_limit()

    @property
    def commit_count(self) -> int:
        return self._commit_count

    @commit_count.setter
    def commit_count(self, value: int):
        self._commit_count = value
        self._page_count = value // self.page_size + 1

    @property
    def counter(self) -> int:
        return self._counter

    def page(self, number: int):
        """
        Retrieves a page of commit logs as an input stream-like object.
        """
        if number > self._page_count:
            raise ValueError(f"Page number: {number} exceeds page count: {self._page_count}")
        if number < 1:
            raise ValueError(f"Page number must be positive! Received {number}")

        skipped_commits = self._commit_count - self.page_size * number
        if skipped_commits < 0:
            number_of_commits = self.page_size + skipped_commits
            skip = 0
        else:
            number_of_commits = self.page_size
            skip = skipped_commits

        logger.debug(
            f"Getting Page {number}/{self._page_count} containing commits "
            f"{skip}-{skip + number_of_commits} of {self._commit_count}"
        )

        return self.git_client.get_n_commit_logs_input_stream(number_of_commits, skip)

    def has_next(self) -> bool:
        return self._counter < self._page_count

    def next(self):
        self._counter += 1
        return self.page(self._counter)

    def reset(self):
        self._counter = 1
