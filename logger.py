import inspect
import logging
import sys
import threading
from typing import Callable, Optional


class CallbackLogHandler(logging.Handler):
    """Forward log records to a callback (e.g. web dashboard SSE). Avoid attaching to loggers that already duplicate to the same sink."""

    def __init__(self, callback: Callable[[str], None]) -> None:
        super().__init__()
        self._callback = callback

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._callback(msg)
        except Exception:  # noqa: BLE001
            self.handleError(record)


def attach_sink_log_handlers(
    callback: Callable[[str], None],
    logger_names: list[str],
    *,
    level: int = logging.INFO,
) -> list[tuple[logging.Logger, CallbackLogHandler]]:
    """Attach one shared handler to each named logger; return pairs for :func:`detach_sink_log_handlers`."""
    handler = CallbackLogHandler(callback)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(levelname)s | %(name)s | %(message)s"))
    pairs: list[tuple[logging.Logger, CallbackLogHandler]] = []
    for name in logger_names:
        lg = logging.getLogger(name)
        lg.addHandler(handler)
        pairs.append((lg, handler))
    return pairs


def detach_sink_log_handlers(pairs: list[tuple[logging.Logger, CallbackLogHandler]]) -> None:
    for lg, h in pairs:
        try:
            lg.removeHandler(h)
        except ValueError:
            pass


def configure_pipeline_logging(level: int = logging.INFO) -> None:
    """
    Configure the root logger for pipeline CLI output.

    Safe to call multiple times: attaches a single StreamHandler if none exist, and
    always applies ``level`` to the root logger and all root handlers (so logging is
    not stuck on a previous level when the root was configured earlier).
    """
    root = logging.getLogger()
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(fmt)
        root.addHandler(handler)
    else:
        for h in root.handlers:
            if h.formatter is None:
                h.setFormatter(fmt)
    root.setLevel(level)
    for h in root.handlers:
        h.setLevel(level)


def get_pipeline_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name if name else "finance_compiler")


class Logger:
    _instance = None
    _lock = threading.Lock()

    PROCESS_NAME_MAP = {
        "fix_null_category": "FIX NULL CATEGORY",
    }

    STYLE_RESET = "\033[0m"
    STYLE_BOLD = "\033[1m"
    STYLE_UNDERLINE = "\033[4m"

    COLOR_BLUE = "\033[34m"
    COLOR_GREEN = "\033[32m"
    COLOR_YELLOW = "\033[33m"

    LOG_TYPES = {
        "started": {
            "prefix": "STARTED",
            "color": COLOR_BLUE,
            "styles": [STYLE_BOLD, STYLE_UNDERLINE]
        },
        "ongoing": {
            "prefix": "ONGOING",
            "color": COLOR_YELLOW,
            "styles": [STYLE_BOLD]
        },
        "finished": {
            "prefix": "FINISHED",
            "color": COLOR_GREEN,
            "styles": [STYLE_BOLD, STYLE_UNDERLINE]
        }
    }

    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
        return cls._instance

    def _get_caller_function_name(self):
        process_name = self.PROCESS_NAME_MAP.get(
            inspect.stack()[0].function,
            inspect.stack()[0].function
        )
        return process_name


    def _format_message(self, log_type, process_name, message, details):
        cfg = self.LOG_TYPES.get(log_type, {})
        prefix = cfg.get("prefix", "ONGOING")
        color = cfg.get("color", self.COLOR_YELLOW)
        styles = cfg.get("styles", [])

        styled_prefix = "".join(styles) + f"--- PROCESS {prefix:<9}:" + self.STYLE_RESET
        msg = f'{styled_prefix} {color}"{process_name}"{self.STYLE_RESET}'
        if message:
            msg += f" {message}"
        if details:
            msg += f" ({details})"
        return msg

    def log_process(self, log_type="ongoing", process_name=None, message="", details=""):
        if not process_name:
            process_name = self._get_caller_function_name()
        print(self._format_message(log_type, process_name.upper().replace("_", " "), message, details))

    def log_process_started(self, process_name=None, message="", details=""):
        self.log_process("started", process_name, message, details)

    def log_process_ongoing(self, process_name=None, message="", details=""):
        self.log_process("ongoing", process_name, message, details)

    def log_process_finished(self, process_name=None, message="", details=""):
        self.log_process("finished", process_name, message, details)

# Usage example:
# def some_function():
#     logger = Logger()
#     logger.log_process_started(message="Initializing database", details="timeout=30s")
#     logger.log_process_ongoing(message="Fetching records from API...")
#     logger.log_process_finished(message="Completed successfully")
