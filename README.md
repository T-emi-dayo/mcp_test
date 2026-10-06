# MCP Server

A production-minded [Model Context Protocol](https://modelcontextprotocol.io) server built on [FastMCP](https://gofastmcp.com). It exposes web search, news search, page scraping and time tools to AI agents over streamable HTTP, with per-agent bearer-token authentication, scope-based authorization, session call budgets, retries and structured JSON logging.

## Features

- **Scope-based access control.** Every tool declares one scope (`web`, `time`, …). Each agent token grants a set of scopes, and FastMCP enforces them on every call.
- **Session call budget.** Caps tool calls per agent session (default 50) to contain runaway agents.
- **Resilience.** Per-tool timeouts and exponential-backoff retries on transient errors (`ConnectionError`, `TimeoutError`, `OSError`).
- **Input validation.** String parameters are length-limited per tool.
- **Provider fallback.** Google Custom Search and NewsAPI are used when keys are present, with automatic fallback to DuckDuckGo, so the tools work with zero API keys.
- **Observability.** Structured JSON logs for requests and server events, request timing, and `/health` and `/health/ready` probes.
- **Low-friction extension.** A tool is a plain typed function with a docstring, registered in one call.

## Tools

| Tool | Scope | Timeout | Description |
| ---- | ----- | ------- | ----------- |
| `search_web` | `web` | 12 s | Web search. Google Custom Search if configured, else DuckDuckGo. Supports `geo_focus` (ISO country code) and `time_horizon` (`last_day`, `last_week`, `last_month`, `last_year`, `all_time`). |
| `search_news` | `web` | 12 s | Recent news. NewsAPI if configured, else DuckDuckGo News. Supports `geo_focus` and `time_horizon`. |
| `scrape_webpage` | `web` | 20 s | Fetches a URL and extracts main content with [trafilatura](https://trafilatura.readthedocs.io) as `txt`, `markdown` or `xml`. Failures are returned in the result's `error` field rather than raised. |
| `get_current_time` | `time` | 6 s | Current date and time from worldtimeapi.org, falling back to the system clock. |

The server also registers an example resource (`greeting://{name}`) and prompt (`greet_user`).

Search and scrape tools return `ToolResult` objects (`title`, `url`, `snippet`, `source`, `date`, `content`, `metadata`, `error`); see [schemas/Tool_Result.py](schemas/Tool_Result.py).

## Requirements

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) (recommended)

## Quick start

```bash
git clone <repo-url>
cd mcp_server

uv sync

cp .env.example .env      # then fill in the agent tokens
uv run python main.py
```

The server listens on `http://0.0.0.0:10000` (override with `PORT`) and serves MCP at the FastMCP default path `/mcp`.

```bash
curl http://localhost:10000/health
# {"status":"ok","server":"MCP Server"}
```

## Configuration

Configuration is read from environment variables or a `.env` file (see [.env.example](.env.example)).

| Variable | Required | Description |
| -------- | -------- | ----------- |
| `MCP_AGENT_TOKEN_RESEARCH`, `_ARISTOTLE`, `_WANDER`, `_CHALLENGE`, `_ADMIN` | Yes | Bearer token for each agent. |
| `PORT` | No | HTTP port. Default `10000`. |
| `GOOGLE_API_KEY`, `GOOGLE_SEARCH_ENGINE_ID` | No | Both needed to enable Google Custom Search. |
| `NEWS_API_KEY` | No | Enables NewsAPI. |

Tunable limits (session budget, timeouts, retry policy, result counts) live in [core/config.py](core/config.py).

## Authentication and scopes

Clients authenticate with `Authorization: Bearer <token>`. Any environment variable named `MCP_AGENT_TOKEN_<NAME>` defines an agent called `<name>`:

- `admin` receives every scope.
- All other agents receive `web`, `compute` and `time`.

Adjust per-agent grants in [core/base.py](core/base.py). Valid scope names are defined in [core/scopes.py](core/scopes.py); registering a tool with an unknown scope fails at startup.

Example client (FastMCP):

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://localhost:10000/mcp", auth="<your-token>") as client:
        result = await client.call_tool("search_web", {"query": "model context protocol"})
        print(result)

asyncio.run(main())
```

## Project layout

```text
main.py              Server manifest: logging, tool registration, startup
core/
  base.py            BaseMCP: auth, middleware stack, register(), health checks
  config.py          Central settings (pydantic-settings)
  scopes.py          Registry of valid scopes
schemas/
  Tool_Result.py     Shared ToolResult model
tools/               One module per tool
```

Request pipeline (outermost first): structured logging → timing → session budget → retry → tool.

## Adding a tool

1. Create `tools/my_tool.py` with a typed function and a docstring. Agents read the docstring to learn how to call it, and registration fails without one.
2. Register it in [main.py](main.py):

   ```python
   from tools.my_tool import my_tool

   server.register(my_tool, scope="web", timeout=10.0, max_input_length=500)
   ```

3. If it needs a new scope, add it to `REGISTERED_SCOPES` in [core/scopes.py](core/scopes.py) and grant it to the relevant agents in [core/base.py](core/base.py).

## Deployment notes

- The server binds to `0.0.0.0` and honours `PORT`, so it runs unchanged on most PaaS hosts.
- CORS currently allows all origins (`allow_origins=["*"]` in [core/base.py](core/base.py)). Restrict it before exposing the server publicly.
- Tokens are static and loaded from the environment. Rotate them by changing the variables and restarting.
- The session budget is tracked in FastMCP session state; it resets when the session ends.

## Status

Early-stage (v0.1.0). `tools/calculator_tool.py` is an empty placeholder for the planned `compute` scope, and there is no automated test suite yet.
