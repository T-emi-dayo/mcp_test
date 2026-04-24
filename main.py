"""
main.py — Server manifest.

Three responsibilities only:
  1. Logging setup
  2. Tool, resource, and prompt registration
  3. Start the server

To add a new tool:
  1. Create tools/your_tool.py — a plain function, typed params, docstring
  2. Import it here
  3. Call server.register() with its scope and any overrides
  Done.
"""

import json
import logging

# Import Base MCP
from core.base import BaseMCP

# Tool Imports
from tools.current_time_tool import get_current_time
from tools.news_search_tool import search_news
from tools.web_scrapping_tool import scrape_webpage
from tools.web_search_tool import search_web

# ── Logging ────────────────────────────────────────────────────────────────────
# Structured JSON logging. Configured before anything else runs.
#
# Note: StructuredLoggingMiddleware handles MCP request/response logging.
# This formatter handles everything outside the request lifecycle —
# startup, registration, warnings, and errors from BaseMCP itself.

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
# Defaults (from config.py): timeout=10.0, max_input_length=500

server.register(
    search_web,
    scope            = "web",
    timeout          = 12.0,   # Search providers can be slow
    max_input_length = 500,
)

server.register(
    get_current_time,
    scope   = "time",
    timeout = 6.0,
    # max_input_length not set — this tool has no string parameters
)

server.register(
    search_news,
    scope            = "web",
    timeout          = 12.0,
    max_input_length = 500,
)

server.register(
    scrape_webpage,
    scope            = "web",
    timeout          = 20.0,   # page fetch + extraction can be slow
    max_input_length = 500,
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