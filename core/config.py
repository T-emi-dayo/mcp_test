"""
config.py — Central configuration for the MCP server.

All tuneable limits live here. Nothing is scattered across files.
To change a limit, change it once here and it takes effect everywhere.
"""

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Client Tokens
    MCP_AGENT_TOKEN_RESEARCH : str
    MCP_AGENT_TOKEN_ARISTOTLE : str
    MCP_AGENT_TOKEN_WANDER : str
    MCP_AGENT_TOKEN_CHALLENGE : str
    MCP_AGENT_TOKEN_ADMIN : str
    
    # ── Session limits ─────────────────────────────────────────────────────────────
    SESSION_MAX_CALLS: int = 50
    
    # ── Tools limits ─────────────────────────────────────────────────────────────
    TOOL_DEFAULT_TIMEOUT : int     = 10.0   # Default no of Seconds before FastMCP kills the tool call
    TOOL_DEFAULT_MAX_INPUT_LEN : int = 500   # Default no of Max characters for any string parameter

    # ── Retry middleware ───────────────────────────────────────────────────────────
    RETRY_MAX_ATTEMPTS : int = 3     # How many total attempts (1 original + 2 retries)
    RETRY_BASE_DELAY : int   = 1.0   # Seconds before first retry
    RETRY_MAX_DELAY  : int   = 16.0  # Backoff ceiling

    # Which exceptions trigger a retry.
    RETRY_EXCEPTIONS : tuple[type[Exception], ...]= (
        ConnectionError,
        TimeoutError,
        OSError,
    )
    
    # Web Search
    WS_MAX_RESULTS: int = 5

    # News Search
    NEWS_BASE_URL: str = "https://newsapi.org/v1"
    NEWS_TIMEOUT: int = 10
    NEWS_MAX_RESULTS: int = 5
    NEWS_API_KEY: str = ""   # optional — empty string means DDGS-only fallback
    
    
    class Config:
       env_file = ".env"
       env_file_encoding = "utf-8"
       
       
settings = Settings()
