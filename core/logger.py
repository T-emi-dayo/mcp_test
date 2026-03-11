import json
import logging
import time

class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""
    def format(self, record: logging.LogRecord) -> str:
        log = {
            "time": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # If extra fields were passed (e.g. tool="search_web"), include them
        for key, val in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            }:
                log[key] = val
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        return json.dumps(log)
    
handler = logging.StreamHandler()
handler.setFormatter(JSONFormatter())
logging.basicConfig(level=logging.INFO, handlers=[handler])
logger = logging.getLogger("mcp_server")
    
# ── Helper: tool call logger ──────────────────────────────────────────────────
def log_tool_start(tool_name: str, client_id: str, **kwargs) -> float:
    logger.info(
        "Tool called",
        extra={"tool": tool_name, "client": client_id, **kwargs}
    )
    return time.time()  # returns start time for duration tracking

def log_tool_end(tool_name: str, client_id: str, start: float, success: bool, **kwargs):
    duration_ms = round((time.time() - start) * 1000)
    logger.info(
        "Tool completed" if success else "Tool failed",
        extra={
            "tool": tool_name,
            "client": client_id,
            "duration_ms": duration_ms,
            "success": success,
            **kwargs
        }
    )