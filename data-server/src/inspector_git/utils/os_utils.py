import platform
from typing import ClassVar


class OsUtils:
    # Cached class-level values (initialized lazily on first access)
    _os_name: ClassVar[str | None] = None
    _is_windows: ClassVar[bool | None] = None
    _is_linux: ClassVar[bool | None] = None
    _is_mac: ClassVar[bool | None] = None
    _is_unix: ClassVar[bool | None] = None
    _command_interpreter_name: ClassVar[str | None] = None
    _interpreter_arg: ClassVar[str | None] = None

    def __new__(cls, *args, **kwargs):
        raise TypeError(f"{cls.__name__} is a static utility class and cannot be instantiated")

    @classmethod
    def _ensure_initialized(cls) -> None:
        """Populate cached values (idempotent)."""
        if cls._os_name is None:
            # platform.system() returns values like 'Windows', 'Linux', 'Darwin'
            cls._os_name = platform.system() or ""
            name_lower = cls._os_name.lower()

            cls._is_windows = "win" in name_lower
            cls._is_linux = "linux" in name_lower or "nux" in name_lower or "nix" in name_lower
            cls._is_mac = "mac" in name_lower or "darwin" in name_lower
            cls._is_unix = cls._is_linux or cls._is_mac

            cls._command_interpreter_name = "bash" if cls._is_unix else "cmd.exe"
            cls._interpreter_arg = "-c" if cls._is_unix else "/C"

    @classmethod
    def os_name(cls) -> str:
        """Return raw OS name string (cached)."""
        cls._ensure_initialized()
        return cls._os_name  # type: ignore[return-value]

    @classmethod
    def is_windows(cls) -> bool:
        cls._ensure_initialized()
        return cls._is_windows  # type: ignore[return-value]

    @classmethod
    def is_linux(cls) -> bool:
        cls._ensure_initialized()
        return cls._is_linux  # type: ignore[return-value]

    @classmethod
    def is_mac(cls) -> bool:
        cls._ensure_initialized()
        return cls._is_mac  # type: ignore[return-value]

    @classmethod
    def is_unix(cls) -> bool:
        cls._ensure_initialized()
        return cls._is_unix  # type: ignore[return-value]

    @classmethod
    def command_interpreter_name(cls) -> str:
        cls._ensure_initialized()
        return cls._command_interpreter_name  # type: ignore[return-value]

    @classmethod
    def interpreter_arg(cls) -> str:
        cls._ensure_initialized()
        return cls._interpreter_arg  # type: ignore[return-value]


# Example (can be removed): simple checks
if __name__ == "__main__":
    print("os_name:", OsUtils.os_name())
    print("is_windows:", OsUtils.is_windows())
    print("is_linux:", OsUtils.is_linux())
    print("is_mac:", OsUtils.is_mac())
    print("is_unix:", OsUtils.is_unix())
    print("command_interpreter_name:", OsUtils.command_interpreter_name())
    print("interpreter_arg:", OsUtils.interpreter_arg())
