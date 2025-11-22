from typing import Optional
from src.inspector_git.reader.enums.line_operation import LineOperation


class LineChangeDTO:
    def __init__(self, operation: LineOperation, number: int, content: Optional[str] = None):
        self.operation = operation
        self.number = number
        self.content = content
