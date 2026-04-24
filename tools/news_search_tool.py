"""
news_search_tool.py
===================
News search with NewsAPI.org (primary) and DuckDuckGo News (fallback).

Backends
--------
- NewsAPI : Requires NEWS_API_KEY env var (free tier: 500 req/day).
            Endpoint: /everything — full-text search across sources.
            Docs: https://newsapi.org/docs/endpoints/everything

- DDGS    : duckduckgo-search news endpoint, no API key required.
            Docs: https://pypi.org/project/duckduckgo-search/

Fallback behaviour
------------------
NewsAPI is used when NEWS_API_KEY is set and non-empty. If the key is
absent or the NewsAPI call fails, the tool transparently falls back to
DuckDuckGo news so callers always receive results.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import requests
from ddgs import DDGS
from duckduckgo_search.exceptions import DuckDuckGoSearchException

from core.config import settings
from schemas.Tool_Result import ToolResult

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

NEWSAPI_BASE_URL    = settings.NEWS_BASE_URL
TIMEOUT             = settings.NEWS_TIMEOUT
DEFAULT_MAX_RESULTS = settings.NEWS_MAX_RESULTS
NEWS_API_KEY        = settings.NEWS_API_KEY

# DDG timelimit codes
_DDG_TIMELIMIT_MAP = {"last_day": "d", "last_week": "w", "last_month": "m", "last_year": "y"}


# ============================================================================
# NEWSAPI BACKEND
# ============================================================================

def _search_newsapi(
    query: str,
    max_results: int,
    geo_focus: Optional[str],
    time_horizon: Optional[str],
    days_back: int = 30,
) -> list[ToolResult]:
    """Fetch news from NewsAPI /everything endpoint."""
    endpoint  = f"{NEWSAPI_BASE_URL}/everything"
    from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    params: dict = {
        "q":        query,
        "apiKey":   NEWS_API_KEY,
        "pageSize": max_results,
        "sortBy":   "publishedAt",
        "language": "en",
        "from":     from_date,
    }

    logger.debug(f"NewsAPI query='{query}' from={from_date}")

    try:
        response = requests.get(endpoint, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.Timeout:
        logger.warning("NewsAPI request timed out")
        raise
    except requests.exceptions.RequestException as e:
        logger.warning(f"NewsAPI request failed: {e}")
        raise

    if data.get("status") != "ok":
        error_msg = data.get("message", "Unknown NewsAPI error")
        raise RuntimeError(f"NewsAPI error: {error_msg}")

    articles = data.get("articles", [])
    logger.debug(f"NewsAPI returned {len(articles)} articles")

    return [
        ToolResult(
            title   = (article.get("title") or "").strip(),
            url     = article.get("url", ""),
            snippet = (article.get("description") or "")[:500],
            source  = article.get("source", {}).get("name") or "newsapi",
            date    = article.get("publishedAt"),
            metadata={
                "query":        query,
                "backend":      "newsapi",
                "geo_focus":    geo_focus,
                "time_horizon": time_horizon,
            },
        )
        for article in articles
        if article.get("title") and article.get("url")
    ]


# ============================================================================
# DUCKDUCKGO NEWS BACKEND
# ============================================================================

def _search_ddgs_news(
    query: str,
    max_results: int,
    geo_focus: Optional[str],
    time_horizon: Optional[str],
) -> list[ToolResult]:
    """Fetch news via DDGS news endpoint."""
    region    = f"{geo_focus.lower()}-en" if geo_focus else "wt-wt"
    timelimit = _DDG_TIMELIMIT_MAP.get(time_horizon or "", None)

    logger.debug(f"DDGS news query='{query}' region={region} timelimit={timelimit}")

    try:
        raw = DDGS().news(
            keywords    = query,
            region      = region,
            safesearch  = "off",
            timelimit   = timelimit,
            max_results = max_results or 10,
        )
    except DuckDuckGoSearchException as e:
        logger.error(f"DDGS news search failed: {e}")
        raise

    items: list[ToolResult] = []
    for article in (raw or []):
        title = (article.get("title") or "").strip()
        url   = (article.get("url") or "").strip()
        if not title or not url:
            continue
        items.append(ToolResult(
            title   = title,
            url     = url,
            snippet = (article.get("body") or "").strip(),
            source  = (article.get("source") or "duckduckgo_news").strip(),
            date    = article.get("date"),
            metadata={
                "query":        query,
                "backend":      "duckduckgo_news",
                "geo_focus":    geo_focus,
                "time_horizon": time_horizon,
            },
        ))

    return items


# ============================================================================
# PUBLIC TOOL FUNCTION
# ============================================================================

def search_news(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    geo_focus: Optional[str] = None,
    time_horizon: Optional[str] = None,
) -> list[ToolResult]:
    """
    Search for recent news articles and return a list of ToolResult objects.

    Uses NewsAPI.org when NEWS_API_KEY is configured. Falls back to
    DuckDuckGo News if the key is absent or if NewsAPI fails.

    Args:
        query:
            News search query. Must not be empty.
        max_results:
            Maximum number of articles to return. Defaults to settings.NEWS_MAX_RESULTS.
        geo_focus:
            Optional 2-letter ISO country code to bias results (e.g. "ng", "us").
            Pass None for worldwide results.
        time_horizon:
            Time window string: "last_day", "last_week", "last_month", "last_year".
            Pass None for no restriction.

    Returns:
        list[ToolResult] — each item has title, url, snippet, source, date,
        and a metadata dict with query context.

    Raises:
        ValueError : query is empty.
        DuckDuckGoSearchException : DDG backend failure after NewsAPI fallback.
    """
    if not query or not query.strip():
        raise ValueError("Query cannot be empty")

    logger.info(
        f"search_news | query='{query}' max={max_results} "
        f"geo={geo_focus} time={time_horizon}"
    )

    if NEWS_API_KEY:
        try:
            return _search_newsapi(query, max_results, geo_focus, time_horizon)
        except Exception as e:
            logger.warning(
                f"NewsAPI failed ({type(e).__name__}: {e}), falling back to DuckDuckGo News"
            )

    logger.debug("Using DuckDuckGo News backend")
    return _search_ddgs_news(query, max_results, geo_focus, time_horizon)
