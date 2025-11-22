import json
from pathlib import Path
from abc import ABC, abstractmethod

# --- Base Class ---
class BaseJsonLoader(ABC):
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)

    def _read_json(self):
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")
        with self.file_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @abstractmethod
    def load(self):
        pass