"""
config.py — Central configuration for the MCP server.

All tuneable limits live here. Nothing is scattered across files.
To change a limit, change it once here and it takes effect everywhere.
"""

# ── Session limits ─────────────────────────────────────────────────────────────
SESSION_MAX_CALLS    = 50      # Hard cap: agent is cut off after this many tool calls
SESSION_TTL_SECONDS  = 1800    # 30 minutes: after this, the session resets automatically

# ── Tool execution limits ──────────────────────────────────────────────────────

TOOL_DEFAULT_TIMEOUT = 10.0    # Seconds a tool is allowed to run before being killed
TOOL_MAX_TIMEOUT     = 30.0    # Ceiling: no tool can declare a timeout higher than this

# ── Retry configuration ────────────────────────────────────────────────────────

RETRY_BASE_DELAY    = 1.0      # Seconds to wait before the first retry
RETRY_MAX_DELAY     = 16.0     # Backoff ceiling — delays never exceed this
RETRY_MAX_ATTEMPTS  = 3        # Default max retries; tool can override downward