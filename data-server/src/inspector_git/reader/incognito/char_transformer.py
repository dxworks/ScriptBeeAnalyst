import os
import random
from pathlib import Path

CHAR_MAP_ENV_VARIABLE = "INCOGNITO_CHARMAP_FILE"
charmap_file_path = os.getenv(CHAR_MAP_ENV_VARIABLE)


class CharTransformer:
    def __init__(self, file_path: str | None = None):
        if file_path:
            self.char_map = self._read_char_map_from_file(Path(file_path))
        else:
            self._check_default_file_exists_and_create_if_necessary()
            self.char_map = self._read_char_map_from_file(self.DEFAULT_FILE)

    def _read_char_map_from_file(self, file: Path) -> dict[str, str]:
        char_map = {}
        try:
            with file.open("r", encoding="utf-8") as f:
                for line in f:
                    parts = line.strip().split()
                    if len(parts) == 2 and len(parts[0]) == 1 and len(parts[1]) == 1:
                        char_map[parts[0]] = parts[1]
        except Exception:
            pass
        return char_map

    def _check_default_file_exists_and_create_if_necessary(self):
        if not self.DEFAULT_FILE.exists():
            self.DEFAULT_FILE.parent.mkdir(parents=True, exist_ok=True)

            letters = list("abcdefghijklmnopqrstuvwxyz")
            numbers = list("0123456789")
            letters_shuffled = random.sample(letters, len(letters))
            numbers_shuffled = random.sample(numbers, len(numbers))

            mapping_lines = [
                f"{c} {letters_shuffled[i]}" for i, c in enumerate(letters)
            ] + [
                f"{c} {numbers_shuffled[i]}" for i, c in enumerate(numbers)
            ]

            self.DEFAULT_FILE.write_text("\n".join(mapping_lines), encoding="utf-8")

    def map_char(self, char: str) -> str:
        if char.isalpha():
            if char.islower() and char in self.char_map:
                return self.char_map[char]
            if char.isupper() and char.lower() in self.char_map:
                return self.char_map[char.lower()].upper()
        elif char.isdigit() and char in self.char_map:
            return self.char_map[char]
        return char

    @property
    def DEFAULT_FILE(self) -> Path:
        if os.getenv("KOTLIN_ENV", "").lower() == "test":
            return Path(".git_incognito/charmap")
        else:
            return Path.home() / ".git_incognito" / "charmap"


if charmap_file_path and Path(charmap_file_path).exists():
    char_transformer = CharTransformer(charmap_file_path)
else:
    char_transformer = CharTransformer()


def encrypt_string(name: str) -> str:
    return "".join(char_transformer.map_char(c) for c in name)
