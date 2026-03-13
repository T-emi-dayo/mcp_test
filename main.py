"""
main.py — Server manifest.

This file does three things only:
  1. Sets up structured logging
  2. Lists which tools, resources, and prompts are registered
  3. Starts the server

It contains no business logic, no auth code, no error handling.
All of that lives in base.py and the individual tool files.

To add a new tool:
  1. Create tools/your_tool.py  (copy the template from base.py)
  2. Import the class here
  3. Call server.register(YourTool)
  That is all.
"""

import json
import logging

from core.server import BaseMCP

# ── Tools ──────────────────────────────────────────────────────────────────────
from tools.web_search_tool import WebSearchTool
from tools.current_time_tool import FetchCurrentTimeTool

# ── Logging setup ──────────────────────────────────────────────────────────────
# Configured before anything else so startup events are captured.

class _JSONFormatter(logging.Formatter):
    """Formats every log record as a single-line JSON object."""
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


# ── Server setup ───────────────────────────────────────────────────────────────

server = BaseMCP(name="MCP Server")

# ── Register tools ─────────────────────────────────────────────────────────────
# Each register() call validates the tool at startup.
# If any tool is misconfigured (missing scope, unknown scope, etc.),
# the server raises immediately here and refuses to start.

server.register(WebSearchTool)
server.register(FetchCurrentTimeTool)

# ── Register resources ─────────────────────────────────────────────────────────

@server.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalised greeting."""
    return f"Hello, {name}!"


# ── Register prompts ───────────────────────────────────────────────────────────

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