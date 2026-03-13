"""
session.py — In-memory session tracking.

A session represents one agent's continuous interaction with the server.
We track how many tool calls have been made and when the session started.
This is how we enforce the agent loop guardrails.

Why in-memory?
    Simple, zero dependencies, and correct for a single Render instance.
    On restart, all sessions reset — which is actually the right behaviour
    for guardrails. A restart is a clean slate.

Why not Redis (yet)?
    Redis adds a moving part. You don't have multiple instances today.
    The interface below is designed so that swapping this class for a
    Redis-backed version later requires changing only this file.
    Nothing in base.py or any tool needs to change.

Thread safety:
    FastMCP handles concurrent requests. Multiple agents can be calling
    tools at the same time. The lock ensures session state stays consistent
    when two requests update the same session simultaneously.
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Dict, Optional

from core.config import SESSION_MAX_CALLS, SESSION_TTL_SECONDS


# ── Session data ───────────────────────────────────────────────────────────────

@dataclass
class Session:
    """
    The state of a single agent session.

    session_id: Identifies this session. Currently the same as client_id
                (one session per agent). If you later want to support
                multiple concurrent sessions per agent (e.g. parallel tasks),
                you can derive session_id as f"{client_id}:{task_id}".

    call_count: Incremented every time the agent successfully passes the
                budget check and calls a tool. The budget check happens
                BEFORE increment, so the 50th call is allowed and the
                51st is blocked.
    """
    session_id:   str
    client_id:    str
    call_count:   int   = 0
    started_at:   float = field(default_factory=time.time)
    last_call_at: float = field(default_factory=time.time)

    def is_expired(self) -> bool:
        """True if this session has been alive longer than SESSION_TTL_SECONDS."""
        return (time.time() - self.started_at) > SESSION_TTL_SECONDS

    def is_over_budget(self) -> bool:
        """True if this session has hit or exceeded the call limit."""
        return self.call_count >= SESSION_MAX_CALLS

    def age_seconds(self) -> int:
        return round(time.time() - self.started_at)

    def remaining_calls(self) -> int:
        return max(0, SESSION_MAX_CALLS - self.call_count)


# ── Store ──────────────────────────────────────────────────────────────────────

class SessionStore:
    """
    Thread-safe in-memory store for active sessions.

    Public interface (the only methods base.py uses):
        get_or_create(session_id, client_id) → Session
        increment(session_id)                → Session
        stats()                              → dict  (for health checks)

    If you ever switch to Redis, implement these three methods with the same
    signatures and nothing else needs to change.
    """

    def __init__(self):
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str, client_id: str) -> Session:
        """
        Returns the existing session, or creates a fresh one.
        Also creates a fresh one if the existing session has expired —
        this is how the 30-minute TTL resets the budget automatically.
        """
        with self._lock:
            session = self._sessions.get(session_id)

            if session is None or session.is_expired():
                session = Session(session_id=session_id, client_id=client_id)
                self._sessions[session_id] = session

            return session

    def increment(self, session_id: str) -> Session:
        """
        Increments the call count for a session.
        Called after the budget check passes — meaning the call is approved
        and is now counted against the session's remaining budget.
        """
        with self._lock:
            session = self._sessions[session_id]
            session.call_count += 1
            session.last_call_at = time.time()
            return session

    def stats(self) -> dict:
        """
        Returns a snapshot of active session state.
        Used by the /health/ready endpoint so operators can see
        which agents are active and how much budget they've consumed.
        """
        with self._lock:
            active = [s for s in self._sessions.values() if not s.is_expired()]
            return {
                "active_sessions": len(active),
                "sessions": [
                    {
                        "session_id":      s.session_id,
                        "client_id":       s.client_id,
                        "call_count":      s.call_count,
                        "remaining_calls": s.remaining_calls(),
                        "age_seconds":     s.age_seconds(),
                    }
                    for s in active
                ]
            }

    def _cleanup_expired(self) -> int:
        """
        Removes expired sessions from memory.
        Not called automatically — call this from a periodic background task
        if you expect very long uptimes with many unique agents.
        Returns the number of sessions removed.
        """
        with self._lock:
            expired_ids = [
                sid for sid, s in self._sessions.items()
                if s.is_expired()
            ]
            for sid in expired_ids:
                del self._sessions[sid]
            return len(expired_ids)


# ── Module-level singleton ─────────────────────────────────────────────────────
# One store for the entire server lifetime. BaseTool imports this directly.
# If you swap to Redis, replace this with a Redis-backed class that implements
# the same three methods. Nothing else changes.

_store = SessionStore()

def get_session_store() -> SessionStore:
    return _store
