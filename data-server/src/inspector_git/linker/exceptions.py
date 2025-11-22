class NoChangeException(Exception):
    def __init__(self, file_name: str) -> None:
        self.file_name = file_name
        super().__init__(f"File {file_name} does not exist!")
