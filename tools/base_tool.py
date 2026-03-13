"""
base.py — BaseMCP with function-based tool registration.

The entire architecture change from v1 is in one method: register().
Everything else — session store, scope validation, the execute pipeline — 
is identical logic to before, just restructured around functions instead of classes.

Tool authors never import from this file.
They write plain functions and pass them to server.register() in main.py.
"""
import time
import random
import logging
import functools
import concurrent.futures
from typing import Callable, Optional, Tuple, Type

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


# ══════════════════════════════════════════════════════════════════════════════
# THE EXECUTE PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
# This is a standalone function, not a method on a class.
# BaseMCP.register() closes over the tool's configuration (scope, timeout,
# retries) and the original function, then uses this pipeline to wrap them.

def _build_wrapped_function(
    fn:           Callable,
    scope:        str,
    timeout:      float,
    max_retries:  int,
    retry_on:     Tuple[Type[Exception], ...],
    max_input_length: int,
) -> Callable:
    """
    Takes a plain tool function and returns a new function that runs the full
    execute pipeline around it.

    The returned function has the same signature, name, and docstring as the
    original. FastMCP sees it as a normal function — it has no idea any
    wrapping occurred.

    Pipeline order:
        1. Scope check
        2. Session budget check
        3. Input validation
        4. Retry loop with timeout
        5. Logging
    """

    @functools.wraps(fn)
    # functools.wraps is the key difference from v1.
    def wrapper(**kwargs):
        tool_name = fn.__name__

        # ── 1. Scope check ─────────────────────────────────────────────────
        # get_access_token() reads from FastMCP's active request context.
        # This is the only place in the entire codebase it is called.
        # Tool functions never see it.
        access_token = get_access_token()
        client_id    = access_token.client_id
        token_scopes = access_token.scopes

        if scope not in token_scopes:
            logger.warning(
                "Scope denied",
                extra={
                    "tool":            tool_name,
                    "client":          client_id,
                    "required_scope":  scope,
                    "token_scopes":    token_scopes,
                }
            )
            return (
                f"ERROR [{tool_name}]: Permission denied. "
                f"This tool requires the '{scope}' scope. "
                f"Your token has: {token_scopes}"
            )

        # ── 2. Session budget check ────────────────────────────────────────
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
                f"Budget resets when the session expires."
            )

        store.increment(session_id)

        # ── 3. Input validation ────────────────────────────────────────────
        # Check all string arguments against the declared max_input_length.
        for param_name, value in kwargs.items():
            if isinstance(value, str) and len(value) > max_input_length:
                logger.warning(
                    "Input validation failed",
                    extra={
                        "tool":   tool_name,
                        "client": client_id,
                        "param":  param_name,
                        "length": len(value),
                        "limit":  max_input_length,
                    }
                )
                return (
                    f"ERROR [{tool_name}]: Parameter '{param_name}' is too long "
                    f"(max {max_input_length} chars, got {len(value)})"
                )

        # ── 4. Retry loop with timeout ─────────────────────────────────────
        effective_timeout = min(timeout, TOOL_MAX_TIMEOUT)
        start_time        = time.time()

        logger.info(
            "Tool called",
            extra={
                "tool":               tool_name,
                "client":             client_id,
                "session_remaining":  session.remaining_calls() - 1,
            }
        )

        for attempt in range(max_retries + 1):
            try:
                # Run the original function in a thread with a timeout.
                # Using a thread because signal-based timeouts only work on
                # the main thread. ThreadPoolExecutor works correctly from
                # any thread, which matters since FastMCP uses a thread pool.
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(fn, **kwargs)
                    try:
                        result = future.result(timeout=effective_timeout)
                    except concurrent.futures.TimeoutError:
                        future.cancel()
                        raise TimeoutError(
                            f"Tool exceeded timeout of {effective_timeout}s"
                        )

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
                is_retryable      = isinstance(e, retry_on)
                has_attempts_left = attempt < max_retries

                if is_retryable and has_attempts_left:
                    delay = min(
                        RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.5),
                        RETRY_MAX_DELAY
                    )
                    logger.warning(
                        "Tool retrying",
                        extra={
                            "tool":    tool_name,
                            "client":  client_id,
                            "attempt": attempt + 1,
                            "delay_s": round(delay, 2),
                            "error":   str(e),
                        }
                    )
                    time.sleep(delay)
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
                return f"ERROR [{tool_name}]: {e}"

        return f"ERROR [{tool_name}]: Unexpected execution failure"

    return wrapper