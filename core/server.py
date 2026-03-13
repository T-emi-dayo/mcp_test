"""
base.py — BaseMCP with function-based tool registration.

The entire architecture change from v1 is in one method: register().
Everything else — session store, scope validation, the execute pipeline — 
is identical logic to before, just restructured around functions instead of classes.

Tool authors never import from this file.
They write plain functions and pass them to server.register() in main.py.
"""

import os
import logging
from typing import Callable, Tuple, Type

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.config import (
    TOOL_DEFAULT_TIMEOUT,
    TOOL_MAX_TIMEOUT,
    RETRY_BASE_DELAY,
    RETRY_MAX_DELAY,
    RETRY_MAX_ATTEMPTS,
    SESSION_MAX_CALLS,
)
from core.scopes import validate_scope
from core.session import get_session_store
from tools.base_tool import _build_wrapped_function

logger = logging.getLogger("mcp_server")

# ══════════════════════════════════════════════════════════════════════════════
# BASE MCP
# ══════════════════════════════════════════════════════════════════════════════

class BaseMCP:
    """
    The server. One instance. Created in main.py.

    Usage:
        server = BaseMCP(name="My Server")

        server.register(my_function, scope="web")
        server.register(another_function, scope="finance", timeout=8.0)

        server.run()
    """

    def __init__(self, name: str = "MCP Server"):
        load_dotenv()

        # ── Auth ───────────────────────────────────────────────────────────
        tokens = {}
        for key, value in os.environ.items():
            if key.startswith("MCP_AGENT_TOKEN_"):
                agent_name = key.replace("MCP_AGENT_TOKEN_", "").lower()
                tokens[value] = {
                    "client_id": agent_name,
                    "scopes":    ["admin"] if agent_name == "admin"
                                 else ["web", "compute", "time"]
                    # ↑ Update these per-agent as you add domain scopes.
                    # A chatbot agent might get ["web", "compute"].
                    # A finance agent might get ["finance", "web"].
                }

        if not tokens:
            logger.warning(
                "No agent tokens loaded — check MCP_AGENT_TOKEN_* env vars"
            )

        auth       = StaticTokenVerifier(tokens=tokens)
        self._mcp  = FastMCP(name=name, auth=auth)
        self._name = name

        self._register_health_checks()

        logger.info("BaseMCP initialised", extra={
            "server_name":   name,
            "tokens_loaded": len(tokens),
        })

    # ── Tool registration ──────────────────────────────────────────────────

    def register(
        self,
        fn:               Callable,
        scope:            str,
        timeout:          float                        = TOOL_DEFAULT_TIMEOUT,
        max_retries:      int                          = RETRY_MAX_ATTEMPTS,
        max_input_length: int                          = 500,
        retry_on:         Tuple[Type[Exception], ...]  = (ConnectionError, TimeoutError, OSError),
    ) -> None:
        """
        Registers a plain function as an MCP tool.

        Args:
            fn:               The tool function. Must have typed parameters
                              and a docstring.
            scope:            Which capability group this tool requires.
                              Must be a value from scopes.REGISTERED_SCOPES.
            timeout:          Seconds before the tool is killed. Default: 10.0.
                              Cannot exceed TOOL_MAX_TIMEOUT (30s).
            max_retries:      How many times to retry on transient failure.
                              Default: 3. Set to 0 for tools where retrying
                              won't help (e.g. pure computation).
            max_input_length: Maximum character length for any string parameter.
                              Default: 500.
            retry_on:         Exception types that trigger a retry.
                              Default: ConnectionError, TimeoutError, OSError.

        Raises:
            ValueError:  If scope is not in REGISTERED_SCOPES.
            RuntimeError: If fn has no docstring (agents need descriptions).
        """

        # ── Startup validation ─────────────────────────────────────────────
        validate_scope(scope, fn.__name__)

        if not fn.__doc__:
            raise RuntimeError(
                f"\n\nTool '{fn.__name__}' has no docstring.\n"
                f"Agents use the docstring to understand what the tool does.\n"
                f"Add a docstring before registering.\n"
            )

        if timeout > TOOL_MAX_TIMEOUT:
            logger.warning(
                f"Tool '{fn.__name__}' declares timeout={timeout}s which exceeds "
                f"TOOL_MAX_TIMEOUT={TOOL_MAX_TIMEOUT}s. Clamping to {TOOL_MAX_TIMEOUT}s."
            )

        # ── Wrap the function with the execute pipeline ────────────────────
        wrapped = _build_wrapped_function(
            fn               = fn,
            scope            = scope,
            timeout          = timeout,
            max_retries      = max_retries,
            retry_on         = retry_on,
            max_input_length = max_input_length,
        )

        # ── Hand to FastMCP ────────────────────────────────────────────────
        # FastMCP receives a function that looks and smells exactly like the
        # original — same name, same docstring, same signature — but runs
        # the full infrastructure pipeline when called.
        self._mcp.tool()(wrapped)

        logger.info("Tool registered", extra={
            "tool":            fn.__name__,
            "required_scope":  scope,
            "timeout":         min(timeout, TOOL_MAX_TIMEOUT),
            "max_retries":     max_retries,
        })

    # ── Resource and prompt pass-throughs ──────────────────────────────────

    def resource(self, uri: str) -> Callable:
        """
        Decorator for registering a resource.

        Usage:
            @server.resource("greeting://{name}")
            def get_greeting(name: str) -> str:
                return f"Hello, {name}!"
        """
        return self._mcp.resource(uri)

    def prompt(self) -> Callable:
        """
        Decorator for registering a prompt.

        Usage:
            @server.prompt()
            def my_prompt(topic: str) -> str:
                return f"Write an essay about {topic}."
        """
        return self._mcp.prompt()

    # ── Health checks ──────────────────────────────────────────────────────

    def _register_health_checks(self) -> None:

        @self._mcp.custom_route("/health", methods=["GET"])
        async def health_liveness(request: Request) -> JSONResponse:
            """Liveness probe — confirms the process is up."""
            return JSONResponse({"status": "ok", "server": self._name})

        @self._mcp.custom_route("/health/ready", methods=["GET"])
        async def health_readiness(request: Request) -> JSONResponse:
            """Readiness probe — checks dependencies are in order."""
            checks = {}
            all_ok = True

            try:
                stats = get_session_store().stats()
                checks["session_store"]    = "ok"
                checks["active_sessions"]  = stats["active_sessions"]
            except Exception as e:
                checks["session_store"] = f"error: {e}"
                all_ok = False

            return JSONResponse(
                {"status": "ready" if all_ok else "degraded", "checks": checks},
                status_code=200 if all_ok else 503,
            )

    # ── Server startup ─────────────────────────────────────────────────────

    def run(self) -> None:
        port = int(os.environ.get("PORT", 10000))

        logger.info("Starting server", extra={
            "server_name": self._name,
            "port":        port,
            "port_source": "env" if os.environ.get("PORT") else "default",
        })

        self._mcp.run(
            transport="streamable-http",
            port=port,
            host="0.0.0.0",
            middleware=[
                Middleware(
                    CORSMiddleware,
                    allow_origins=["*"],
                    allow_credentials=True,
                    allow_methods=["*"],
                    allow_headers=["*"],
                )
            ]
        )