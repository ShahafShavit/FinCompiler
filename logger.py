import inspect
import threading


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
        return self.PROCESS_NAME_MAP.get(
            inspect.stack()[0].function,
            inspect.stack()[0].function
        )

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
        print(self._format_message(log_type, process_name, message, details))

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
