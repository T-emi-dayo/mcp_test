import os
import logging
import inspect
from dotenv import load_dotenv
from typing import Type

from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier

from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.scopes import REGISTERED_SCOPES, validate_scope
from tools.base import BaseTool
from core.config import TOOL_MAX_TIMEOUT

logger = logging.getLogger("mcp_server")

# ══════════════════════════════════════════════════════════════════════════════
# BASE MCP
# ══════════════════════════════════════════════════════════════════════════════

class BaseMCP:
    """
    The server.

    Responsibilities:
      - Loads environment and sets up auth
      - Holds the FastMCP instance
      - Provides register() to onboard tools
      - Provides run() to start the server
      - Exposes /health and /health/ready endpoints

    Usage:
        server = BaseMCP()
        server.register(WebSearchTool)
        server.register(FetchCurrentTimeTool)
        server.run()
    """

    def __init__(self, name: str = "MCP Server"):
        # load_dotenv FIRST — must happen before we read any env vars
        load_dotenv()

        # ── Auth setup ─────────────────────────────────────────────────────
        tokens = {}
        for key, value in os.environ.items():
            if key.startswith("MCP_AGENT_TOKEN_"):
                agent_name = key.replace("MCP_AGENT_TOKEN_", "").lower()
                tokens[value] = {
                    "client_id": agent_name,
                    "scopes":    ["admin"] if agent_name == "admin"
                                 else ["read", "write"]
                    # ↑ These scopes are the legacy read/write model.
                    # As you add domain scopes (web, finance, etc.),
                    # update this to assign the appropriate capability scopes.
                    # Example: ["web", "compute"] for a chatbot agent.
                }

        if not tokens:
            logger.warning(
                "No agent tokens loaded. "
                "Check that MCP_AGENT_TOKEN_* environment variables are set."
            )

        auth = StaticTokenVerifier(tokens=tokens)

        # ── FastMCP instance ────────────────────────────────────────────────
        self._mcp  = FastMCP(name=name, auth=auth)
        self._name = name

        # ── Register built-in health check routes ───────────────────────────
        self._register_health_checks()

        logger.info(
            "BaseMCP initialised",
            extra={"name": name, "tokens_loaded": len(tokens)}
        )

    # ── Tool registration ──────────────────────────────────────────────────

    def register(self, tool_class: Type[BaseTool]) -> None:
        """
        Registers a tool class with the server.

        What this does:
          1. Validates the tool's required_scope exists in the registry
          2. Validates the tool has a run() implementation
          3. Enforces the timeout ceiling
          4. Creates a wrapper function with the correct signature for FastMCP
          5. Registers the wrapper with FastMCP under the tool's name

        The wrapper is the bridge between FastMCP's decorator world and your
        class-based tool. FastMCP never sees the class — it sees a function
        with the right signature and docstring. The function calls execute(),
        which calls run(). Tool authors see none of this.
        """

        # ── Startup validation ─────────────────────────────────────────────
        # These checks run once at startup, not on every request.
        # A bad tool declaration causes an immediate, descriptive error.

        if tool_class.required_scope is None:
            raise RuntimeError(
                f"\n\nTool '{tool_class.__name__}' has not declared required_scope.\n"
                f"Add this to the class:\n"
                f"    required_scope = '<scope>'\n"
                f"Valid scopes: {sorted(REGISTERED_SCOPES)}\n"
            )

        validate_scope(tool_class.required_scope, tool_class.__name__)

        if tool_class.timeout > TOOL_MAX_TIMEOUT:
            logger.warning(
                f"Tool '{tool_class.__name__}' declares timeout={tool_class.timeout}s "
                f"which exceeds TOOL_MAX_TIMEOUT={TOOL_MAX_TIMEOUT}s. "
                f"Clamping to {TOOL_MAX_TIMEOUT}s."
            )

        tool_name = tool_class.get_tool_name()

        # ── Create tool instance ───────────────────────────────────────────
        # One instance per tool class, shared across all requests.
        # run() must be stateless — never store request-specific data as instance attributes inside run().
        tool_instance = tool_class()

        # ── Build wrapper function ─────────────────────────────────────────
        # FastMCP infers the tool's JSON schema from the wrapper's signature.
        # We copy run()'s signature (minus 'self') onto the wrapper so FastMCP
        # generates the correct parameter schema for agents to use.

        run_sig    = inspect.signature(tool_instance.run)
        params     = [p for name, p in run_sig.parameters.items() if name != "self"]
        new_sig    = run_sig.replace(parameters=params)

        def wrapper(**kwargs):
            return tool_instance.execute(**kwargs)

        # Apply the correct signature and metadata
        wrapper.__signature__ = new_sig
        wrapper.__name__      = tool_name
        wrapper.__doc__       = tool_instance.run.__doc__

        # Register with FastMCP
        self._mcp.tool()(wrapper)

        logger.info(
            "Tool registered",
            extra={
                "tool":           tool_name,
                "class":          tool_class.__name__,
                "required_scope": tool_class.required_scope,
                "timeout":        min(tool_class.timeout, TOOL_MAX_TIMEOUT),
                "max_retries":    tool_class.max_retries,
            }
        )

    # ── Resource and prompt registration (pass-through) ───────────────────

    def resource(self, uri: str):
        """
        Pass-through decorator for registering resources.
        Resources are read-only and simpler than tools — no retry or timeout needed.
        Scope checking for resources can be added here in a future iteration.

        Usage:
            @server.resource("greeting://{name}")
            def get_greeting(name: str) -> str:
                return f"Hello, {name}!"
        """
        return self._mcp.resource(uri)

    def prompt(self):
        """
        Pass-through decorator for registering prompts.
        Prompts are pure text generation — no external calls, no side effects.

        Usage:
            @server.prompt()
            def greet_user(name: str, style: str = "friendly") -> str:
                ...
        """
        return self._mcp.prompt()

    # ── Health checks ──────────────────────────────────────────────────────

    def _register_health_checks(self) -> None:
        """
        Registers /health and /health/ready as HTTP routes on the FastMCP server.

        /health       Liveness probe. Proves the process is up and the web
                      framework is accepting requests. Always fast. This is
                      what Render's health check should point to.

        /health/ready Readiness probe. Checks that dependencies are in order.
                      Returns 503 if something is misconfigured. Use this for
                      deeper monitoring or startup validation.
        """

        @self._mcp.custom_route("/health", methods=["GET"])
        async def health_liveness(request: Request) -> JSONResponse:
            return JSONResponse({"status": "ok", "server": self._name})

        @self._mcp.custom_route("/health/ready", methods=["GET"])
        async def health_readiness(request: Request) -> JSONResponse:
            from core.session import get_session_store
            checks  = {}
            all_ok  = True

            # Check: are tokens configured?
            from fastmcp.server.auth import StaticTokenVerifier
            checks["auth"] = "ok"  # If server is running, auth is configured

            # Check: session store accessible?
            try:
                session_stats = get_session_store().stats()
                checks["session_store"] = "ok"
                checks["active_sessions"] = session_stats["active_sessions"]
            except Exception as e:
                checks["session_store"] = f"error: {e}"
                all_ok = False

            return JSONResponse(
                {"status": "ready" if all_ok else "degraded", "checks": checks},
                status_code=200 if all_ok else 503
            )

    # ── Server startup ─────────────────────────────────────────────────────

    def run(self) -> None:
        """Starts the MCP server."""
        port = int(os.environ.get("PORT", 10000))

        logger.info(
            "Starting server",
            extra={
                "server": self._name,
                "port":   port,
                "host":   "0.0.0.0",
                "port_source": "env" if os.environ.get("PORT") else "default",
            }
        )

        self._mcp.run(
            transport="streamable-http",
            port=port,
            host="0.0.0.0",
            middleware=[
                Middleware(
                    CORSMiddleware,
                    allow_origins=["*"],   # TODO: restrict to known origins
                    allow_credentials=True,
                    allow_methods=["*"],
                    allow_headers=["*"],
                )
            ]
        )
