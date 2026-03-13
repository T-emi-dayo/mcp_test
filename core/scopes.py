"""
scopes.py — The authoritative registry of valid capability scopes.

A scope is a named capability group. Every tool must declare exactly one.
Agents are granted one or more scopes via their token.
The base class enforces this automatically — tool authors never write auth checks.

To add a new scope:     add it to REGISTERED_SCOPES below. That is all.
To retire a scope:      remove it — the server will reject tools that declare it at startup.
To rename a scope:      add the new name, update all tool declarations and tokens,
                        remove the old name. 
"""

from typing import FrozenSet

# ── Scope definitions ──────────────────────────────────────────────────────────

REGISTERED_SCOPES: FrozenSet[str] = frozenset({

    "web",       # Public internet: search, fetch, scrape
                 # Example tools: search_web, fetch_url, scrape_page

    "finance",   # Financial data: market prices, exchange rates, portfolios

    "compute",   # Computation: math eval, unit conversion, calculations

    "time",      

    "internal",  

    "comms",     
})


# ── Validation ─────────────────────────────────────────────────────────────────

def validate_scope(scope: str, tool_name: str) -> None:
    """
    Called at server startup when each tool is registered.
    Raises immediately if the scope is not in the registry.

    This is the safety net that prevents two failure modes:
    1. A typo in required_scope ("webb" instead of "web") going undetected
    2. A tool being registered without any scope at all

    Because this runs at startup, not at request time, the error is caught
    before any agent ever calls the tool. The server simply refuses to start.
    """
    if scope not in REGISTERED_SCOPES:
        raise ValueError(
            f"\n\nTool '{tool_name}' declares unknown scope '{scope}'.\n"
            f"Valid scopes are: {sorted(REGISTERED_SCOPES)}\n"
            f"To add a new scope, edit scopes.py — REGISTERED_SCOPES.\n"
        )
