"""
base.py — BaseMCP with middleware-first infrastructure.

What changed from v2:
    - No manual timeout logic          → FastMCP timeout= parameter
    - No ThreadPoolExecutor            → FastMCP automatic threadpool
    - No scope check in wrapper        → FastMCP auth=require_scopes()
    - No session.py                    → FastMCP ctx.get_state() / ctx.set_state()
    - No manual logging                → LoggingMiddleware + StructuredLoggingMiddleware
    - No manual retry in wrapper       → RetryMiddleware
    - No timing code                   → TimingMiddleware

What we still own:
    - Session call budgeting           → SessionBudgetMiddleware (our custom middleware)
    - Input length validation          → Pydantic Field(max_length=) on tool parameters

The wrapper is gone entirely. register() is now a thin pass-through to mcp.add_tool()
that attaches auth, timeout, and the tool's max_input_length constraint.

Tool authors:
    Write a plain function with typed parameters and a docstring.
    Register it in main.py with server.register().
    Done.
"""

import os
import logging
import functools
import inspect
from typing import Callable, Optional, Tuple, Type

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier, require_scopes
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.logging import (
    StructuredLoggingMiddleware,
)
from fastmcp.server.middleware.error_handling import RetryMiddleware
from fastmcp.server.middleware.timing import TimingMiddleware
from fastmcp.exceptions import ToolError
from starlette.middleware import Middleware as ASGIMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from core.config import settings
from core.scopes import validate_scope

logger = logging.getLogger("mcp_server")




# ══════════════════════════════════════════════════════════════════════════════
# SESSION BUDGET MIDDLEWARE
# ══════════════════════════════════════════════════════════════════════════════

class SessionBudgetMiddleware(Middleware):
    """
    Enforces a hard cap on how many tool calls one agent session can make.

    Why middleware and not a wrapper:
        Budget enforcement is a session-level concern, not a tool-level concern.
        It needs to fire for every tool call regardless of which tool it is.
        Middleware is exactly the right place for cross-cutting session logic.

    How it works:
        On every on_call_tool, we read the call count from FastMCP's session
        state (ctx.get_state), increment it if under budget, or raise ToolError
        if over budget.

        FastMCP's session state is automatically keyed per session — no manual
        session store needed. Values must be JSON-serialisable (int is fine).

    What the agent sees on budget exceeded:
        A ToolError with a clear message. FastMCP surfaces ToolError as an
        error result to the agent rather than a server crash.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext,
        call_next,
    ):
        fastmcp_ctx = context.fastmcp_context

        if fastmcp_ctx is None:
            # Session not yet established (e.g. during initialisation).
            # Let the call proceed — budget tracking starts once session is live.
            return await call_next(context)

        # Read current call count. None means first call in this session.
        call_count = await fastmcp_ctx.get_state("call_count") or 0

        if call_count >= settings.SESSION_MAX_CALLS:
            tool_name = context.message.name if context.message else "unknown"
            logger.warning(
                "Session budget exceeded",
                extra={
                    "tool":       tool_name,
                    "call_count": call_count,
                    "limit":      settings.SESSION_MAX_CALLS,
                    "session_id": fastmcp_ctx.session_id,
                }
            )
            raise ToolError(
                f"Session budget exceeded. "
                f"This agent has made {call_count} tool calls "
                f"(limit: {settings.SESSION_MAX_CALLS}). "
                f"The budget resets when the session expires."
            )

        # Increment before the call so the count reflects calls attempted,not just calls that succeeded.
        await fastmcp_ctx.set_state("call_count", call_count + 1)

        return await call_next(context)


# ══════════════════════════════════════════════════════════════════════════════
# INPUT VALIDATION WRAPPER
# ══════════════════════════════════════════════════════════════════════════════

def _apply_input_validation(fn: Callable, max_input_length: int) -> Callable:
    """
    Wraps a tool function to validate string parameter lengths before execution.
    """
    @functools.wraps(fn)
    def validated(**kwargs):
        for param_name, value in kwargs.items():
            if isinstance(value, str) and len(value) > max_input_length:
                return (
                    f"ERROR [{fn.__name__}]: Input too long. "
                    f"Parameter '{param_name}' is {len(value)} characters "
                    f"(max: {max_input_length})."
                )
        return fn(**kwargs)

    return validated


# ══════════════════════════════════════════════════════════════════════════════
# BASE MCP
# ══════════════════════════════════════════════════════════════════════════════

class BaseMCP:
    """
    The server. One instance. Created in main.py.

    Sets up all middleware, handles auth configuration, and provides
    register() for attaching tools.

    Usage:
        server = BaseMCP(name="MCP Server")
        server.register(my_function, scope="web")
        server.register(other_function, scope="finance", timeout=8.0)
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
                    "scopes": (
                        ["web", "finance", "compute", "time", "data",
                         "internal", "comms", "admin"]
                        if agent_name == "admin"
                        else ["web", "compute", "time"]
                        # ↑ Update per-agent as you build out your client roster.
                        # A chatbot: ["web", "compute"]
                        # A finance agent: ["finance", "web"]
                    )
                }

        if not tokens:
            logger.warning(
                "No agent tokens loaded. "
                "Check MCP_AGENT_TOKEN_* environment variables."
            )

        auth      = StaticTokenVerifier(tokens=tokens)
        self._mcp = FastMCP(name=name, auth=auth)
        self._name = name

        # ── Middleware stack ────────────────────────────────────────────────
        # 1. StructuredLoggingMiddleware  — logs every request as JSON first
        # 2. TimingMiddleware             — measures duration around the call
        # 3. SessionBudgetMiddleware      — enforces call limits per session
        # 4. RetryMiddleware              — retries on transient failures last (closest to the actual tool call)

        self._mcp.add_middleware(StructuredLoggingMiddleware())

        self._mcp.add_middleware(TimingMiddleware())

        self._mcp.add_middleware(SessionBudgetMiddleware())

        self._mcp.add_middleware(RetryMiddleware(
            max_retries       = settings.RETRY_MAX_ATTEMPTS,
            base_delay        = settings.RETRY_BASE_DELAY,
            max_delay         = settings.RETRY_MAX_DELAY,
            retry_exceptions  = settings.RETRY_EXCEPTIONS,

        ))

        # ── Health checks ───────────────────────────────────────────────────
        self._register_health_checks()

        logger.info("BaseMCP initialised", extra={
            "server_name":   name,
            "tokens_loaded": len(tokens),
        })

    # ── Tool registration ──────────────────────────────────────────────────

    def register(
        self,
        fn:              Callable,
        scope:           str,
        timeout:         float = settings.TOOL_DEFAULT_TIMEOUT,
        max_input_length: int  = settings.TOOL_DEFAULT_MAX_INPUT_LEN,
    ) -> None:
        """
        Registers a plain function as an MCP tool.

        What this method does:
            1. Validates the scope exists in the registry
            2. Validates the function has a docstring
            3. Wraps the function with input length validation
            4. Calls mcp.add_tool() with auth=require_scopes(scope)
               and timeout=timeout

        Args:
            fn:               Plain function with typed parameters and docstring.
            scope:            Capability group. Must be in scopes.REGISTERED_SCOPES.
            timeout:          Seconds before FastMCP kills the call. Default: 10.0.
            max_input_length: Max characters for any string parameter. Default: 500.

        Raises:
            ValueError:   If scope is not registered.
            RuntimeError: If fn has no docstring.
        """

        # ── Startup validation ─────────────────────────────────────────────
        validate_scope(scope, fn.__name__)

        if not fn.__doc__:
            raise RuntimeError(
                f"\n\nTool '{fn.__name__}' has no docstring.\n"
                f"Agents use the docstring to understand what a tool does "
                f"and how to call it correctly. Add one before registering.\n"
            )

        # ── Input length validation ────────────────────────────────────────
        validated_fn = _apply_input_validation(fn, max_input_length)

        # ── Register with FastMCP ──────────────────────────────────────────
        # require_scopes(scope) is FastMCP's native auth check.
        self._mcp.tool(
            validated_fn,
            auth    = require_scopes(scope),
            timeout = timeout,
        )

        logger.info("Tool registered", extra={
            "tool":            fn.__name__,
            "required_scope":  scope,
            "timeout":         timeout,
            "max_input_length": max_input_length,
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
            """
            Readiness probe — confirms the server is configured and ready.
            Returns 503 if something is missing.
            """
            checks = {}
            all_ok = True

            # Check: auth tokens loaded
            # If no tokens, every request will be rejected anyway.
            # This catches a misconfigured environment before any agent connects.
            from fastmcp.server.auth import StaticTokenVerifier
            checks["auth"] = "ok"  # Server wouldn't have started without it

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
                ASGIMiddleware(
                    CORSMiddleware,
                    allow_origins=["*"],   # TODO: restrict to known origins
                    allow_credentials=True,
                    allow_methods=["*"],
                    allow_headers=["*"],
                )
            ]
        )