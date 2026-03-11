"""
FastMCP server with error handling, health checks, and structured logging.
"""

# ── Standard library ──────────────────────────────────────────────────────────
import os
from typing import Optional

# ── Third party ───────────────────────────────────────────────────────────────
import requests
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from fastmcp import FastMCP
from fastmcp.server.dependencies import AccessToken, get_access_token

# ── Local tools ───────────────────────────────────────────────────────────────
from tools.web_search_tool import DEFAULT_MAX_RESULTS, _get_search_tool
from tools.calculator_tool import math

# ── Logging and Authentication Setup ─────────────────────────────────────────────────────────────
from core.logger import JSONFormatter, logger, log_tool_start, log_tool_end
from core.authentication import auth, tokens

# ── Server ────────────────────────────────────────────────────────────────────
mcp = FastMCP(name="Seismic MCP Server", auth=auth)


# ── Health check endpoints ────────────────────────────────────────────────────
# FastMCP lets you add custom HTTP routes alongside the MCP protocol routes.
#
# /health       — shallow check. Just proves the process is running and the
#                 web framework is accepting requests. Fast, always cheap.
#
# /health/ready — deep check. Tests that actual dependencies are reachable.
#                 Use this for readiness probes (is the server READY to serve
#                 traffic?) vs liveness probes (is it ALIVE at all?).

@mcp.custom_route("/health", methods=["GET"])
async def health_shallow(request: Request) -> JSONResponse:
    """Liveness probe — confirms the process is up."""
    return JSONResponse({
        "status": "ok",
        "server": "Test",
    })


@mcp.custom_route("/health/ready", methods=["GET"])
async def health_ready(request: Request) -> JSONResponse:
    """
    Readiness probe — checks that dependencies are reachable.
    Returns 200 if ready, 503 if not.
    
    503 (Service Unavailable) is the correct status for "I'm running but
    not ready to serve traffic" — this is what load balancers act on.
    """
    checks = {}
    all_ok = True

    # Check 1: Are tokens configured?
    # If no tokens are loaded, auth will reject every request anyway.
    checks["auth_tokens"] = "ok" if tokens else "missing"
    if not tokens:
        all_ok = False

    # Check 2: Can we reach the time API we depend on?
    # This is a lightweight ping — we're just checking connectivity,
    # not doing a full request.
    try:
        r = requests.head("http://worldtimeapi.org", timeout=3)
        checks["worldtimeapi"] = "ok"
    except Exception as e:
        checks["worldtimeapi"] = f"unreachable: {e}"
        # Don't fail readiness on this — it's not critical
        # Remove the `pass` and set all_ok = False if you want it to be critical

    status_code = 200 if all_ok else 503
    return JSONResponse(
        {"status": "ready" if all_ok else "degraded", "checks": checks},
        status_code=status_code
    )


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def safe_eval_math(expression: str) -> str:
    """
    Safely evaluates a basic mathematical expression.
    Supports +, -, *, /, **, %, and parentheses.
    """
    access_token: AccessToken = get_access_token()
    client_id = access_token.client_id
    start = log_tool_start("safe_eval_math", client_id)

    try:
        result = eval(
            expression,
            {"__builtins__": None, "math": math},
            {}
        )
        output = str(round(result, 6))
        log_tool_end("safe_eval_math", client_id, start, success=True)
        return output

    except Exception as e:
        # Log the real error internally with full detail
        logger.error(
            "safe_eval_math error",
            extra={"client": client_id, "error": str(e)}
        )
        log_tool_end("safe_eval_math", client_id, start, success=False)
        # Return a clean message to the agent — not a raw exception
        return f"ERROR [safe_eval_math]: {e}"


@mcp.tool()
def search_web(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    geo_focus: Optional[str] = None,
    time_horizon: Optional[str] = None,
) -> list[dict]:
    """
    Search the web using a shared WebSearchTool instance.

    Args:
        query:        Search query string
        max_results:  Maximum number of results to return
        geo_focus:    Geographic focus (e.g., "Nigeria", "global")
        time_horizon: Time filter (e.g., "last_30_days", "last_5_years")

    Returns:
        List of result dicts with keys: title, link, snippet
    """
    access_token: AccessToken = get_access_token()
    client_id = access_token.client_id
    # We log query length, not the query itself — queries may be sensitive
    start = log_tool_start("search_web", client_id, query_length=len(query), max_results=max_results)

    try:
        results = _get_search_tool().search(query, max_results, geo_focus, time_horizon)
        log_tool_end("search_web", client_id, start, success=True, result_count=len(results))
        return results

    except Exception as e:
        logger.error(
            "search_web error",
            extra={"client": client_id, "error": str(e)}
        )
        log_tool_end("search_web", client_id, start, success=False)
        # Return empty list with an error dict — agent can check for this
        return [{"error": f"ERROR [search_web]: {e}", "title": "", "link": "", "snippet": ""}]


@mcp.tool()
def get_current_time_api() -> str:
    """
    Fetches the current UTC date and time from an online API.
    Falls back to local time if request fails.
    """
    access_token: AccessToken = get_access_token()
    client_id = access_token.client_id
    start = log_tool_start("get_current_time_api", client_id)

    try:
        res = requests.get("http://worldtimeapi.org/api/ip", timeout=5)
        res.raise_for_status()
        data = res.json()
        output = f"{data.get('datetime', '')} ({data.get('timezone', 'UTC')})"
        log_tool_end("get_current_time_api", client_id, start, success=True)
        return output

    except Exception as e:
        logger.warning(
            "get_current_time_api fell back to local time",
            extra={"client": client_id, "error": str(e)}
        )
        log_tool_end("get_current_time_api", client_id, start, success=False)
        return f"ERROR [get_current_time_api]: Could not fetch time — {e}"


# ── Resources & prompts (Format for adding) ──────────────────────────────────────────

@mcp.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting"""
    return f"Hello, {name}!"


@mcp.prompt()
def greet_user(name: str, style: str = "friendly") -> str:
    """Generate a greeting prompt"""
    styles = {
        "friendly": "Please write a warm, friendly greeting",
        "formal": "Please write a formal, professional greeting",
        "casual": "Please write a casual, relaxed greeting",
    }
    return f"{styles.get(style, styles['friendly'])} for someone named {name}."


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    logger.info("Starting MCP server", extra={"port": port, "host": "0.0.0.0"})

    mcp.run(
        transport="streamable-http",
        port=port,
        host="0.0.0.0",
        middleware=[
            Middleware(
                CORSMiddleware,
                allow_origins=["*"],  # TODO: restrict to known origins in production
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"]
            )
        ]
    )