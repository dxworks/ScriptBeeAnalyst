from abc import ABC, abstractmethod
from io import StringIO

class IGWriter(ABC):
    """
    Abstract writer class for building a string response.
    Subclasses should implement `append_lines` to add content to the buffer.
    """
    def __init__(self, incognito: bool = False):
        self._incognito = incognito
        self._response_builder = StringIO()

    def write(self) -> str:
        """
        Builds and returns the final string by calling append_lines.
        """
        self.append_lines(self._response_builder)
        return self._response_builder.getvalue()

    @abstractmethod
    def append_lines(self, response_builder: StringIO):
        """
        Append lines to the response builder.
        Must be implemented by subclasses.
        """
        pass
