"""
main.py — Server manifest.

Three responsibilities only:
  1. Logging setup
  2. Tool, resource, and prompt registration
  3. Start the server

To add a new tool:
  1. Create tools/your_tool.py — a plain function with typed params and a docstring
  2. Import it here
  3. Call server.register() with its scope and any overrides
  Done.
"""

import json
import logging

from core.server import BaseMCP

from tools.web_search_tool import search_web
from tools.current_time_tool import get_current_time

# ── Logging ────────────────────────────────────────────────────────────────────

class _JSONFormatter(logging.Formatter):
    _SKIP = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        log = {
            "time":    self.formatTime(record),
            "level":   record.levelname,
            "logger":  record.name,
            "message": record.getMessage(),
        }
        for key, val in record.__dict__.items():
            if key not in self._SKIP:
                log[key] = val
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        return json.dumps(log)


_handler = logging.StreamHandler()
_handler.setFormatter(_JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])


# ── Server ─────────────────────────────────────────────────────────────────────

server = BaseMCP(name="MCP Server")


# ── Tools ──────────────────────────────────────────────────────────────────────
# register(function, scope, **overrides)
# Only specify overrides that differ from the defaults in config.py.
# Defaults: timeout=10.0, max_retries=3, max_input_length=500

server.register(
    search_web,
    scope       = "web",
    timeout     = 12.0,    # Search providers can be slow
    max_retries = 3,
)

server.register(
    get_current_time,
    scope       = "time",
    timeout     = 6.0,
    max_retries = 3,
)


# ── Resources ──────────────────────────────────────────────────────────────────

@server.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalised greeting."""
    return f"Hello, {name}!"


# ── Prompts ────────────────────────────────────────────────────────────────────

@server.prompt()
def greet_user(name: str, style: str = "friendly") -> str:
    """Generate a greeting prompt in the requested style."""
    styles = {
        "friendly": "Please write a warm, friendly greeting",
        "formal":   "Please write a formal, professional greeting",
        "casual":   "Please write a casual, relaxed greeting",
    }
    return f"{styles.get(style, styles['friendly'])} for someone named {name}."


# ── Start ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    server.run()