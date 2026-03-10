"""
FastMCP quickstart example.

Run from the repository root:
    uv run examples/snippets/servers/fastmcp_quickstart.py
"""

# MCP Server imports
from importlib.resources import path
import os

from fastmcp import FastMCP
import requests
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware import Middleware

# Authenticaation imports
from starlette.responses import JSONResponse
from starlette.requests import Request as StarletteRequest 
from fastmcp.server.auth import StaticTokenVerifier
from fastmcp.server.dependencies import AccessToken, get_access_token
from dotenv import load_dotenv
from typing import Optional

# Tool imports
from tools.web_search_tool import DEFAULT_MAX_RESULTS, _get_search_tool
from tools.calculator_tool import math, SAFE_OPERATORS


# Authentication
tokens = {}
for key, value in os.environ.items():
    if key.startswith("MCP_AGENT_TOKEN_"):
        agent_name = key.replace("MCP_AGENT_TOKEN_", "").lower()
        tokens[value] = {
            "client_id": agent_name,
            "scopes": ["admin"] if agent_name == "admin" else ["read", "write"]
        }

auth = StaticTokenVerifier(tokens=tokens)

# Create an MCP server
mcp = FastMCP(name = "Test", auth= auth)

load_dotenv()  # Load environment variables from .env file


# Add an addition tool
@mcp.tool()
def safe_eval_math(expression: str) -> str:
    """
    Safely evaluates a basic mathematical expression.
    Supports +, -, *, /, **, %, and parentheses.
    """
    
    access_token: AccessToken = get_access_token()
    user_id = access_token.client_id
    
    try:
        # Restrict builtins, allow math functions
        result = eval(
            expression,
            {"__builtins__": None, "math": math},
            {}
        )
        return str(round(result, 6))
    except Exception as e:
        return f"Calculation error: {e}"

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
    user_id = access_token.client_id
    
    return _get_search_tool().search(query, max_results, geo_focus, time_horizon)

# Add a dynamic greeting resource
@mcp.resource("greeting://{name}")
def get_greeting(name: str) -> str:
    """Get a personalized greeting"""
    return f"Hello, {name}!"


# Add a prompt
@mcp.prompt()
def greet_user(name: str, style: str = "friendly") -> str:
    """Generate a greeting prompt"""
    styles = {
        "friendly": "Please write a warm, friendly greeting",
        "formal": "Please write a formal, professional greeting",
        "casual": "Please write a casual, relaxed greeting",
    }

    return f"{styles.get(style, styles['friendly'])} for someone named {name}."

@mcp.tool()
def get_current_time_api() -> str:
    """
    Fetches the current UTC date and time from an online API.
    Falls back to local time if request fails.
    """
    try:
        res = requests.get("http://worldtimeapi.org/api/ip", timeout=5)
        res.raise_for_status()
        data = res.json()
        current_time = data.get("datetime", "")
        timezone = data.get("timezone", "UTC")
        return f"{current_time} ({timezone})"
    
    except Exception as e:
        return f"Could not fetch time from API: {e}"

# Run with streamable HTTP transport
if __name__ == "__main__":
    mcp.run(transport="streamable-http",
            port= 10000,
            host= "0.0.0.0",
            middleware=[
                Middleware(CORSMiddleware, 
                           allow_origins=["*"], 
                           allow_credentials=True, 
                           allow_methods=["*"], 
                           allow_headers=["*"])
                ]
            )