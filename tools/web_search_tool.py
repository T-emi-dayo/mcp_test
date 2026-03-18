"""
web_search_tool.py
==================
A clean, dependency-light web search tool with Google Custom Search and
DuckDuckGo fallback.

Backends
--------
- Google  : Direct HTTP to the Custom Search JSON API (no wrappers).
            Requires GOOGLE_API_KEY + GOOGLE_SEARCH_ENGINE_ID env vars.
            Docs: https://developers.google.com/custom-search/v1/reference/rest/v1/cse/list

- DuckDuckGo : `duckduckgo-search` library (DDGS class), no API key required.
               Docs: https://pypi.org/project/duckduckgo-search/
               Install: pip install duckduckgo-search httpx

No LangChain. No intermediate wrappers.
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Optional

import httpx
from duckduckgo_search import DDGS
from duckduckgo_search.exceptions import DuckDuckGoSearchException, RatelimitException, TimeoutException
from pydantic import BaseModel, Field

from core.config import settings

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

GOOGLE_API_ENDPOINT = "https://customsearch.googleapis.com/customsearch/v1"
DEFAULT_MAX_RESULTS: int = settings.WS_MAX_RESULTS
HTTP_TIMEOUT: int = 10  # seconds


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
    Docs: https://developers.google.com/custom-search/v1/reference/rest/v1/cse/list
    """
    LAST_DAY   = "last_day"
    LAST_WEEK  = "last_week"
    LAST_MONTH = "last_month"
    LAST_YEAR  = "last_year"
    ALL_TIME   = "all_time"


# ============================================================================
# PYDANTIC MODELS
# ============================================================================

class SearchResult(BaseModel):
    """A single normalised search result."""
    title:   str            = Field(description="Result title")
    url:     str            = Field(description="Result URL")
    snippet: str            = Field(description="Short description or excerpt")
    source:  SearchBackend  = Field(description="Which backend returned this result")
    date:    Optional[str]  = Field(default=None, description="Publication date if available")


class SearchResponse(BaseModel):
    """The full response returned by search_web()."""
    query:         str               = Field(description="The original search query")
    backend_used:  SearchBackend     = Field(description="Backend that fulfilled the request")
    total_results: int               = Field(description="Number of results returned")
    results:       list[SearchResult] = Field(default_factory=list)


# ============================================================================
# TIME HORIZON MAPPINGS
# ============================================================================

# Google dateRestrict: d[n], w[n], m[n], y[n]
_GOOGLE_TIME_MAP: dict[TimeHorizon, str | None] = {
    TimeHorizon.LAST_DAY:   "d1",
    TimeHorizon.LAST_WEEK:  "w1",
    TimeHorizon.LAST_MONTH: "m1",
    TimeHorizon.LAST_YEAR:  "y1",
    TimeHorizon.ALL_TIME:   None,
}

# DuckDuckGo timelimit: d, w, m, y
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
) -> list[SearchResult]:
    """
    Call the Google Custom Search JSON API directly via httpx.

    Key API constraints (from official docs):
      - `num`         : results per request — valid range 1-10 (max 10).
      - `start`       : 1-based pagination index; max useful value is 91
                        since start + num cannot exceed 101.
      - `gl`          : 2-letter ISO country code to geo-bias results.
      - `dateRestrict`: d1/w1/m1/y1 for time filtering.
      - Response body : items[].{title, link, snippet}
    """
    api_key   = os.environ["GOOGLE_API_KEY"]
    engine_id = os.environ["GOOGLE_SEARCH_ENGINE_ID"]

    results: list[SearchResult] = []
    start = 1  # Google pagination is 1-based

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
            # gl expects a 2-letter ISO country code
            params["gl"] = geo_focus.lower()[:2]

        date_restrict = _GOOGLE_TIME_MAP.get(time_horizon)
        if date_restrict:
            params["dateRestrict"] = date_restrict

        logger.debug(f"Google CSE → start={start}, num={batch}")
        response = httpx.get(GOOGLE_API_ENDPOINT, params=params, timeout=HTTP_TIMEOUT)
        response.raise_for_status()

        items = response.json().get("items") or []
        if not items:
            break  # No more results available

        for item in items:
            results.append(SearchResult(
                title   = item.get("title", "").strip(),
                url     = item.get("link", ""),
                snippet = item.get("snippet", "").strip().replace("\n", " "),
                source  = SearchBackend.GOOGLE,
                date    = None,  # Google CSE does not expose a date field
            ))

        start += len(items)

        # Google hard cap: start + num must not exceed 101
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
) -> list[SearchResult]:
    """
    Search via duckduckgo-search (DDGS class).

    Key API notes (from official docs):
      - DDGS().text() → List[Dict] with keys: title, href, body
      - `region`    : "{country}-{lang}", e.g. "wt-wt" (worldwide), "us-en", "ng-en"
      - `timelimit` : "d", "w", "m", "y" — or None for all time
      - `backend`   : "auto" aggregates across available backends
      - Exception types: RatelimitException, TimeoutException < DuckDuckGoSearchException
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
        SearchResult(
            title   = item.get("title", "").strip(),
            url     = item.get("href", ""),
            snippet = item.get("body", "").strip(),
            source  = SearchBackend.DUCKDUCKGO,
            date    = None,  # DDGS.text() does not return a date field
        )
        for item in (raw or [])
    ]


# ============================================================================
# PUBLIC CONVENIENCE FUNCTION
# ============================================================================

def search_web(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    geo_focus: Optional[str] = None,
    time_horizon: TimeHorizon = TimeHorizon.ALL_TIME,
) -> SearchResponse:
    """
    Search the web and return a structured SearchResponse.

    Selects Google Custom Search if GOOGLE_API_KEY and GOOGLE_SEARCH_ENGINE_ID
    are present in the environment. Falls back to DuckDuckGo otherwise.

    Args:
        query:
            Search query string. Must not be empty.

        max_results:
            Maximum number of results. Google is capped at 100 total (10 per
            page, max 10 pages). DuckDuckGo has no hard cap but quality
            degrades beyond ~50. Defaults to settings.WS_MAX_RESULTS.

        geo_focus:
            Optional 2-letter ISO country code to bias results geographically.
            Examples: "ng" (Nigeria), "us" (USA), "gb" (UK).
            Pass None for worldwide results (default).

        time_horizon:
            Time window for results. Use the TimeHorizon enum.
            Defaults to TimeHorizon.ALL_TIME (no restriction).

    Returns:
        SearchResponse — contains query, backend_used, total_results,
        and a list of SearchResult Pydantic models.

    Raises:
        ValueError               : query is empty.
        httpx.HTTPStatusError    : Google API returned a non-2xx response.
        RatelimitException       : DuckDuckGo rate limit exceeded.
        TimeoutException         : Request timed out.
        DuckDuckGoSearchException: Any other DuckDuckGo error.

    Example:
        >>> from web_search_tool import search_web, TimeHorizon
        >>> response = search_web("AI regulation Nigeria", geo_focus="ng", time_horizon=TimeHorizon.LAST_MONTH)
        >>> for r in response.results:
        ...     print(r.title, r.url)
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

    if google_ready:
        logger.debug("Backend selected: Google Custom Search")
        try:
            results = _google_search(query, max_results, geo_focus, time_horizon)
        except httpx.HTTPStatusError as e:
            logger.error(f"Google API HTTP error {e.response.status_code}: {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Google search failed ({type(e).__name__}): {e}")
            raise
    else:
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

    return SearchResponse(
        query         = query,
        backend_used  = SearchBackend.GOOGLE if google_ready else SearchBackend.DUCKDUCKGO,
        total_results = len(results),
        results       = results,
    )