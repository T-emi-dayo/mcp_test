"""
web_search_tool.py
==================
Web search with Google Custom Search (primary) and DuckDuckGo (fallback).

Backends
--------
- Google  : Direct HTTP to the Custom Search JSON API (no wrappers).
            Requires GOOGLE_API_KEY + GOOGLE_SEARCH_ENGINE_ID env vars.
            Docs: https://developers.google.com/custom-search/v1/reference/rest/v1/cse/list

- DuckDuckGo : duckduckgo-search library (DDGS), no API key required.
               Docs: https://pypi.org/project/duckduckgo-search/

Fallback behaviour
------------------
Google is selected when both env vars are present. If Google fails at
runtime (quota, outage, HTTP error), the call transparently falls back to
DuckDuckGo so callers always receive results.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Optional

import httpx
from duckduckgo_search import DDGS
from duckduckgo_search.exceptions import DuckDuckGoSearchException, RatelimitException, TimeoutException

from core.config import settings
from schemas.Tool_Result import ToolResult

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

GOOGLE_API_ENDPOINT = "https://customsearch.googleapis.com/customsearch/v1"
DEFAULT_MAX_RESULTS: int = settings.WS_MAX_RESULTS
HTTP_TIMEOUT: int = 10


# ============================================================================
# ENUMS
# ============================================================================

class SearchBackend(str, Enum):
    GOOGLE     = "google"
    DUCKDUCKGO = "duckduckgo"


class TimeHorizon(str, Enum):
    """
    Time restriction options.

    Google dateRestrict syntax : d[n], w[n], m[n], y[n]
    DuckDuckGo timelimit       : d, w, m, y
    """
    LAST_DAY   = "last_day"
    LAST_WEEK  = "last_week"
    LAST_MONTH = "last_month"
    LAST_YEAR  = "last_year"
    ALL_TIME   = "all_time"


# ============================================================================
# TIME HORIZON MAPPINGS
# ============================================================================

_GOOGLE_TIME_MAP: dict[TimeHorizon, str | None] = {
    TimeHorizon.LAST_DAY:   "d1",
    TimeHorizon.LAST_WEEK:  "w1",
    TimeHorizon.LAST_MONTH: "m1",
    TimeHorizon.LAST_YEAR:  "y1",
    TimeHorizon.ALL_TIME:   None,
}

_DDG_TIME_MAP: dict[TimeHorizon, str | None] = {
    TimeHorizon.LAST_DAY:   "d",
    TimeHorizon.LAST_WEEK:  "w",
    TimeHorizon.LAST_MONTH: "m",
    TimeHorizon.LAST_YEAR:  "y",
    TimeHorizon.ALL_TIME:   None,
}


# ============================================================================
# GOOGLE BACKEND
# ============================================================================

def _google_search(
    query: str,
    max_results: int,
    geo_focus: Optional[str],
    time_horizon: TimeHorizon,
) -> list[ToolResult]:
    """
    Call the Google Custom Search JSON API directly via httpx.

    Key API constraints:
      - num   : results per request, valid range 1-10.
      - start : 1-based pagination; max useful value is 91 (start + num <= 101).
      - gl    : 2-letter ISO country code for geo-bias.
      - dateRestrict : d1/w1/m1/y1 for time filtering.
    """
    api_key   = os.environ["GOOGLE_API_KEY"]
    engine_id = os.environ["GOOGLE_SEARCH_ENGINE_ID"]

    results: list[ToolResult] = []
    start = 1

    while len(results) < max_results:
        batch = min(10, max_results - len(results))

        params: dict = {
            "key":   api_key,
            "cx":    engine_id,
            "q":     query,
            "num":   batch,
            "start": start,
        }

        if geo_focus:
            params["gl"] = geo_focus.lower()[:2]

        date_restrict = _GOOGLE_TIME_MAP.get(time_horizon)
        if date_restrict:
            params["dateRestrict"] = date_restrict

        logger.debug(f"Google CSE → start={start}, num={batch}")
        response = httpx.get(GOOGLE_API_ENDPOINT, params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()

        items = response.json().get("items") or []
        if not items:
            break

        for item in items:
            results.append(ToolResult(
                title   = item.get("title", "").strip(),
                url     = item.get("link", ""),
                snippet = item.get("snippet", "").strip().replace("\n", " "),
                source  = SearchBackend.GOOGLE,
            ))

        start += len(items)
        if start > 91:
            break

    return results[:max_results]


# ============================================================================
# DUCKDUCKGO BACKEND
# ============================================================================

def _ddg_search(
    query: str,
    max_results: int,
    geo_focus: Optional[str],
    time_horizon: TimeHorizon,
) -> list[ToolResult]:
    """
    Search via duckduckgo-search (DDGS).

    - region    : "{country}-en", e.g. "us-en", "ng-en"; "wt-wt" for worldwide.
    - timelimit : "d", "w", "m", "y" or None.
    - backend   : "auto" aggregates across available backends.
    """
    region    = f"{geo_focus.lower()}-en" if geo_focus else "wt-wt"
    timelimit = _DDG_TIME_MAP.get(time_horizon)

    logger.debug(f"DDG → region={region}, timelimit={timelimit}")

    raw = DDGS(timeout=HTTP_TIMEOUT).text(
        query,
        region      = region,
        safesearch  = "off",
        timelimit   = timelimit,
        max_results = max_results,
        backend     = "auto",
    )

    return [
        ToolResult(
            title   = item.get("title", "").strip(),
            url     = item.get("href", ""),
            snippet = item.get("body", "").strip(),
            source  = SearchBackend.DUCKDUCKGO,
        )
        for item in (raw or [])
    ]


# ============================================================================
# PUBLIC TOOL FUNCTION
# ============================================================================

def search_web(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    geo_focus: Optional[str] = None,
    time_horizon: TimeHorizon = TimeHorizon.ALL_TIME,
) -> list[ToolResult]:
    """
    Search the web and return a list of ToolResult objects.

    Uses Google Custom Search when GOOGLE_API_KEY and GOOGLE_SEARCH_ENGINE_ID
    are set. Falls back to DuckDuckGo if the keys are absent or if Google
    fails at runtime (quota, outage, HTTP error).

    Args:
        query:
            Search query string. Must not be empty.
        max_results:
            Maximum number of results to return. Defaults to settings.WS_MAX_RESULTS.
        geo_focus:
            Optional 2-letter ISO country code to bias results (e.g. "ng", "us").
            Pass None for worldwide results.
        time_horizon:
            Time window for results. Defaults to TimeHorizon.ALL_TIME.

    Returns:
        list[ToolResult] — each item has title, url, snippet, source,
        and a metadata dict with query context.

    Raises:
        ValueError : query is empty.
        RatelimitException : DuckDuckGo rate limit exceeded (no fallback available).
        DuckDuckGoSearchException : DDG backend failure after Google fallback.
    """
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")

    logger.info(
        f"search_web | query='{query}' max={max_results} "
        f"geo={geo_focus} time={time_horizon.value}"
    )

    google_ready = bool(
        os.getenv("GOOGLE_API_KEY") and os.getenv("GOOGLE_SEARCH_ENGINE_ID")
    )

    backend_used = SearchBackend.DUCKDUCKGO
    results: list[ToolResult] = []

    if google_ready:
        logger.debug("Backend selected: Google Custom Search")
        try:
            results = _google_search(query, max_results, geo_focus, time_horizon)
            backend_used = SearchBackend.GOOGLE
        except Exception as e:
            logger.warning(
                f"Google search failed ({type(e).__name__}: {e}), falling back to DuckDuckGo"
            )
            # Fall through to DuckDuckGo below

    if not results:
        logger.debug("Backend selected: DuckDuckGo")
        try:
            results = _ddg_search(query, max_results, geo_focus, time_horizon)
        except RatelimitException:
            logger.error("DuckDuckGo rate limit hit")
            raise
        except TimeoutException:
            logger.error("DuckDuckGo request timed out")
            raise
        except DuckDuckGoSearchException as e:
            logger.error(f"DuckDuckGo search failed: {e}")
            raise

    shared_meta = {
        "query":        query,
        "backend":      backend_used.value,
        "total_results": len(results),
        "geo_focus":    geo_focus,
        "time_horizon": time_horizon.value,
    }
    for r in results:
        r.metadata = shared_meta

    return results
