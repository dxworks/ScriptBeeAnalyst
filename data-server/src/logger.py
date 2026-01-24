import logging
import os
import sys

# Get log level from environment variable, default to INFO
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_LEVEL_NUM = getattr(logging, LOG_LEVEL, logging.INFO)

# ANSI color codes
class LogColors:
    RESET = "\033[0m"

    # Processor colors (Cyan theme)
    PROCESSOR = "\033[96m"  # Bright Cyan
    PROCESSOR_BOLD = "\033[1;96m"

    # API/Server colors (Green theme)
    SERVER = "\033[92m"  # Bright Green
    SERVER_BOLD = "\033[1;92m"

    # Log level colors
    DEBUG = "\033[37m"     # White
    INFO = "\033[94m"      # Blue
    WARNING = "\033[93m"   # Yellow
    ERROR = "\033[91m"     # Red
    CRITICAL = "\033[1;91m"  # Bold Red


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors based on logger name and level."""

    LEVEL_COLORS = {
        logging.DEBUG: LogColors.DEBUG,
        logging.INFO: LogColors.INFO,
        logging.WARNING: LogColors.WARNING,
        logging.ERROR: LogColors.ERROR,
        logging.CRITICAL: LogColors.CRITICAL,
    }

    def format(self, record):
        # Choose color based on logger name
        if "processor" in record.name.lower():
            name_color = LogColors.PROCESSOR_BOLD
        elif "server" in record.name.lower() or "api" in record.name.lower():
            name_color = LogColors.SERVER_BOLD
        else:
            name_color = LogColors.RESET

        # Choose color based on log level
        level_color = self.LEVEL_COLORS.get(record.levelno, LogColors.RESET)

        # Format: [TIMESTAMP] [LEVEL] NAME: MESSAGE
        timestamp = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        level_name = record.levelname
        logger_name = record.name
        message = record.getMessage()

        # Apply colors
        formatted = (
            f"{LogColors.DEBUG}[{timestamp}]{LogColors.RESET} "
            f"{level_color}[{level_name}]{LogColors.RESET} "
            f"{name_color}{logger_name}{LogColors.RESET}: "
            f"{message}"
        )

        # Add exception info if present
        if record.exc_info:
            formatted += "\n" + self.formatException(record.exc_info)

        return formatted


# Configure root logger
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredFormatter())

logging.basicConfig(
    level=LOG_LEVEL_NUM,
    handlers=[handler],
    force=True  # Override any existing config
)

def get_logger(name: str) -> logging.Logger:
    """Return a logger with the given name."""
    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL_NUM)
    return logger
