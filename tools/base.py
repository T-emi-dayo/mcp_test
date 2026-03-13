"""
base.py — BaseMCP and BaseTool.

This is the engine. Tool authors never need to read or edit this file.
They inherit from BaseTool, declare a few class attributes, implement run(),
and everything else happens automatically.

Reading this file is only necessary if you are:
  - Maintaining the server infrastructure
  - Adding a new cross-cutting concern to the execute pipeline
  - Debugging an unexpected behaviour in tool execution

Structure:
  BaseMCP   — The server. Created once. Owns the FastMCP instance,
              session store, and the tool registration logic.

  BaseTool  — The base class every tool inherits from. Owns the execute()
              pipeline: scope check → session check → validation →
              retry loop with timeout → logging.
"""

import re
import time
import random
import logging
import inspect
import concurrent.futures
from abc import abstractmethod
from typing import Optional, Tuple, Type

from fastmcp.server.dependencies import get_access_token

from core.config import (
    TOOL_DEFAULT_TIMEOUT,
    TOOL_MAX_TIMEOUT,
    RETRY_BASE_DELAY,
    RETRY_MAX_DELAY,
    RETRY_MAX_ATTEMPTS,
    SESSION_MAX_CALLS,
)

from core.session import get_session_store

logger = logging.getLogger("mcp_server")


# ── Utility ────────────────────────────────────────────────────────────────────

def _class_to_tool_name(class_name: str) -> str:
    """
    Converts a class name to a snake_case tool name.
    Strips the 'Tool' suffix if present.

    Examples:
        WebSearchTool    → web_search
        ExchangeRateTool → exchange_rate
        TimeTool         → time
        SafeEvalMath     → safe_eval_math   (no Tool suffix)
    """
    # Strip 'Tool' suffix
    name = class_name[:-4] if class_name.endswith("Tool") else class_name
    # CamelCase → snake_case
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


# ══════════════════════════════════════════════════════════════════════════════
# BASE TOOL
# ══════════════════════════════════════════════════════════════════════════════

class BaseTool:
    """
    The base class every tool on this server inherits from.

    What tool authors must provide:
        required_scope  (str)   — Which capability group this tool requires.
                                  Must be a value from scopes.REGISTERED_SCOPES.
        run()           (method)— The tool's actual business logic.
                                  No error handling, no auth, no logging needed.

    What tool authors may optionally override:
        timeout         (float) — Seconds before the tool is killed. Default: 10.0.
                                  Cannot exceed TOOL_MAX_TIMEOUT (30s).
        max_retries     (int)   — How many times to retry on transient failure. Default: 3.
        retry_on        (tuple) — Exception types that trigger a retry.
        max_input_length(int)   — Maximum length for any string parameter. Default: 500.
        tool_name       (str)   — Override the auto-derived snake_case name if needed.

    What tool authors must NOT do:
        - Write try/except in run()     (base class handles all exceptions)
        - Call get_access_token()       (base class handles auth)
        - Write logging                 (base class handles logging)
        - Write retry logic             (base class handles retries)
        - Check scopes                  (base class handles scope enforcement)

    The contract: run() contains ONLY the logic that makes this tool unique.
    """

    # ── Class-level attributes tool authors declare ────────────────────────
    # required_scope has no default. Omitting it causes a startup error.
    required_scope:   Optional[str]           = None

    timeout:          float                   = TOOL_DEFAULT_TIMEOUT
    max_retries:      int                     = RETRY_MAX_ATTEMPTS
    max_input_length: int                     = 500
    tool_name:        Optional[str]           = None   # Auto-derived if not set

    # Exceptions that warrant a retry. ConnectionError and TimeoutError cover
    # the vast majority of transient network failures.
    # Non-retryable errors (ValueError, KeyError, etc.) are caught and returned
    # as clean error strings without retrying.
    retry_on: Tuple[Type[Exception], ...] = (
        ConnectionError,
        TimeoutError,
        OSError,
    )

    # ── Tool name ──────────────────────────────────────────────────────────

    @classmethod
    def get_tool_name(cls) -> str:
        """Returns the name this tool will be registered under with FastMCP."""
        if cls.tool_name:
            return cls.tool_name
        return _class_to_tool_name(cls.__name__)

    # ── Business logic — tool authors implement this ───────────────────────

    @abstractmethod
    def run(self, **kwargs):
        """
        Implement the tool's actual logic here.

        Rules:
          - Do not catch exceptions — let them propagate, the base class handles them.
          - Do not log — the base class logs every call.
          - Do not check scopes — the base class enforces them.
          - Return the result directly. On the rare occasion your logic detects
            a business-level error (not an exception), return a string starting
            with "ERROR:". The base class returns all other errors automatically.
        """
        raise NotImplementedError

    # ── Input validation ───────────────────────────────────────────────────

    def _validate_inputs(self, kwargs: dict) -> Optional[str]:
        """
        Checks all string parameters against max_input_length.
        Returns an error message if any parameter fails, None if all pass.

        This is a baseline check. For tool-specific constraints (e.g. an integer
        must be between 1 and 20), use Pydantic Field constraints in run()'s
        parameter annotations instead.
        """
        sig = inspect.signature(self.run)
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            value = kwargs.get(param_name)
            if value is not None and isinstance(value, str):
                if len(value) > self.max_input_length:
                    return (
                        f"Parameter '{param_name}' is too long "
                        f"(max {self.max_input_length} chars, got {len(value)})"
                    )
        return None

    # ── Timeout-enforced execution ─────────────────────────────────────────

    def _run_with_timeout(self, effective_timeout: float, **kwargs):
        """
        Runs self.run(**kwargs) in a thread with a timeout.

        Why a thread? Python's timeout mechanisms (signal.alarm) only work on
        the main thread. ThreadPoolExecutor.future.result(timeout=N) works
        correctly regardless of which thread the call comes from, which matters
        because FastMCP dispatches requests from a thread pool.

        If the timeout fires, we cancel the future (best-effort — Python threads
        cannot be forcibly killed, but the future result is discarded) and raise
        TimeoutError, which is in retry_on and will be handled by the retry loop.
        """
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.run, **kwargs)
            try:
                return future.result(timeout=effective_timeout)
            except concurrent.futures.TimeoutError:
                future.cancel()
                raise TimeoutError(
                    f"Tool exceeded timeout of {effective_timeout}s"
                )

    # ── The execute pipeline ───────────────────────────────────────────────

    def execute(self, **kwargs):
        """
        The full execution pipeline. Called by the wrapper FastMCP registers.
        Tool authors never call this directly.

        Pipeline:
            1. Scope check          — does this token have the required scope?
            2. Session budget check — has this agent hit their call limit?
            3. Input validation     — are the inputs within declared constraints?
            4. Retry loop           — run with timeout, retry transient failures
            5. Logging              — every call logged with outcome and duration
        """
        tool_name = self.get_tool_name()

        # ── 1. Scope check ─────────────────────────────────────────────────
        # get_access_token() is a FastMCP dependency — it reads the token from
        # the current request context. It only works inside a live request.
        access_token = get_access_token()
        client_id    = access_token.client_id
        token_scopes: list = access_token.scopes

        if self.required_scope not in token_scopes:
            logger.warning(
                "Scope denied",
                extra={
                    "tool":           tool_name,
                    "client":         client_id,
                    "required_scope": self.required_scope,
                    "token_scopes":   token_scopes,
                }
            )
            return (
                f"ERROR [{tool_name}]: Permission denied. "
                f"This tool requires the '{self.required_scope}' scope. "
                f"Your token has: {token_scopes}"
            )

        # ── 2. Session budget check ────────────────────────────────────────
        # session_id = client_id keeps it simple: one budget per agent token.
        # To support multiple concurrent sessions per agent, derive a
        # session_id that includes a task or conversation identifier.
        session_id = client_id
        store      = get_session_store()
        session    = store.get_or_create(session_id, client_id)

        if session.is_over_budget():
            logger.warning(
                "Session budget exceeded",
                extra={
                    "tool":       tool_name,
                    "client":     client_id,
                    "call_count": session.call_count,
                    "limit":      SESSION_MAX_CALLS,
                }
            )
            return (
                f"ERROR [{tool_name}]: Session budget exceeded. "
                f"This agent has made {session.call_count} calls "
                f"(limit: {SESSION_MAX_CALLS}). "
                f"The budget resets after the session expires."
            )

        # Budget is available — count this call now.
        store.increment(session_id)

        # ── 3. Input validation ────────────────────────────────────────────
        validation_error = self._validate_inputs(kwargs)
        if validation_error:
            logger.warning(
                "Input validation failed",
                extra={"tool": tool_name, "client": client_id, "error": validation_error}
            )
            return f"ERROR [{tool_name}]: Invalid input — {validation_error}"

        # ── 4. Retry loop with timeout ─────────────────────────────────────
        # Enforce the ceiling — tool cannot declare a timeout above TOOL_MAX_TIMEOUT
        effective_timeout = min(self.timeout, TOOL_MAX_TIMEOUT)
        start_time        = time.time()
        last_error        = None

        logger.info(
            "Tool called",
            extra={
                "tool":       tool_name,
                "client":     client_id,
                "session_remaining": session.remaining_calls() - 1,
            }
        )

        for attempt in range(self.max_retries + 1):
            try:
                result = self._run_with_timeout(effective_timeout, **kwargs)

                # ── 5. Log success ─────────────────────────────────────────
                duration_ms = round((time.time() - start_time) * 1000)
                logger.info(
                    "Tool completed",
                    extra={
                        "tool":        tool_name,
                        "client":      client_id,
                        "duration_ms": duration_ms,
                        "attempt":     attempt + 1,
                        "success":     True,
                    }
                )
                return result

            except Exception as e:
                is_retryable = isinstance(e, self.retry_on)
                has_attempts_left = attempt < self.max_retries

                if is_retryable and has_attempts_left:
                    # Exponential backoff with jitter.
                    # Jitter prevents multiple agents from retrying in sync
                    # after a shared dependency recovers.
                    delay = min(
                        RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5),
                        RETRY_MAX_DELAY
                    )
                    logger.warning(
                        "Tool retrying",
                        extra={
                            "tool":     tool_name,
                            "client":   client_id,
                            "attempt":  attempt + 1,
                            "delay_s":  round(delay, 2),
                            "error":    str(e),
                        }
                    )
                    time.sleep(delay)
                    last_error = e
                    continue

                # ── 5. Log failure ─────────────────────────────────────────
                duration_ms = round((time.time() - start_time) * 1000)
                logger.error(
                    "Tool failed",
                    extra={
                        "tool":        tool_name,
                        "client":      client_id,
                        "duration_ms": duration_ms,
                        "attempt":     attempt + 1,
                        "retryable":   is_retryable,
                        "error":       str(e),
                        "success":     False,
                    }
                )

                if is_retryable:
                    return (
                        f"ERROR [{tool_name}]: Failed after {attempt + 1} attempts. "
                        f"Last error: {e}"
                    )
                else:
                    return f"ERROR [{tool_name}]: {e}"

        # Should not reach here, but defensive fallback
        return f"ERROR [{tool_name}]: Unexpected execution failure"