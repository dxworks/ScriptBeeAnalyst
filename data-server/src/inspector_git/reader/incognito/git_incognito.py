import re
import shutil
from pathlib import Path

from .char_transformer import encrypt_string

author_regex = re.compile(r"(author:)(.*)")
email_regex = re.compile(r"(email:)(.*)(@.*)")


def process_git_log_file_incognito(
    log_file: Path, destination: Path | None = None, encoding: str = "utf-8"
):
    incognito_file = log_file.with_name(f"{log_file.stem}-incognito.git")
    print(f"Processing incognito Git log from {log_file} to {incognito_file}")

    try:
        with log_file.open("r", encoding=encoding, errors="strict") as infile, incognito_file.open(
            "w", encoding=encoding
        ) as writer:
            for line in infile:
                line = line.rstrip("\n")
                author_match = author_regex.match(line)
                email_match = email_regex.match(line)

                if author_match:
                    new_line = author_match.group(1) + encrypt_string(author_match.group(2))
                elif email_match:
                    new_line = (
                        email_match.group(1)
                        + encrypt_string(email_match.group(2))
                        + email_match.group(3)
                    )
                else:
                    new_line = line
                writer.write(new_line + "\n")

    except UnicodeDecodeError:
        if encoding.lower() == "utf-8":
            return process_git_log_file_incognito(log_file, destination, encoding="iso-8859-1")

    shutil.copyfile(incognito_file, destination or log_file)
    incognito_file.unlink(missing_ok=True)
