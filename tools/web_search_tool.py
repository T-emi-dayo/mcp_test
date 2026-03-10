import json
import logging
import os
from functools import lru_cache
from typing import Optional

from langchain_google_community import GoogleSearchAPIWrapper
from langchain_community.tools import DuckDuckGoSearchResults

logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================
DEFAULT_MAX_RESULTS = 5
TIMEOUT = 30

TIME_HORIZON_MAP = {
    "last_30_days": "last 30 days",
    "last_year":    "last year",
    "last_5_years": "last 5 years",
}


# ============================================================================
# SEARCH WEB TOOL
# ============================================================================

class WebSearchTool:
    """
    Domain-agnostic web search tool with Google + DuckDuckGo fallback.

    - Uses Google Search API if GOOGLE_API_KEY and GOOGLE_SEARCH_ENGINE_ID are set
    - Falls back to DuckDuckGo otherwise
    """

    def __init__(self):
        google_api_key = os.getenv("GOOGLE_API_KEY")
        google_engine_id = os.getenv("GOOGLE_SEARCH_ENGINE_ID")
        self.google_available = bool(google_api_key and google_engine_id)

        self.duckduckgo = DuckDuckGoSearchResults(
            output_format="list",
            max_results=DEFAULT_MAX_RESULTS,
            backend="text",
            source="news",
        )

        if self.google_available:
            self.google = GoogleSearchAPIWrapper()
            logger.info("✅ Google Search API available")
        else:
            logger.info("⚠️  Google credentials not found, using DuckDuckGo")

    def _build_query(self, query: str, geo_focus: Optional[str], time_horizon: Optional[str]) -> str:
        """Append geo and time modifiers to the base query."""
        parts = [query]

        if geo_focus and geo_focus.lower() != "global":
            parts.append(geo_focus)

        if time_horizon:
            modifier = next(
                (label for key, label in TIME_HORIZON_MAP.items() if key in time_horizon),
                None
            )
            if modifier:
                parts.append(modifier)

        enhanced = " ".join(parts)
        logger.debug(f"Enhanced query: '{enhanced}'")
        return enhanced

    def _parse_google_results(self, results: list[dict]) -> list[dict]:
        """
        Normalize Google results to a consistent dict format.
        Google's API already returns structured dicts — just ensure required keys exist.
        """
        return [
            {
                "title":   r.get("title", ""),
                "link":    r.get("link", ""),
                "snippet": r.get("snippet", ""),
            }
            for r in results
            if isinstance(r, dict)
        ]

    def _parse_duckduckgo_results(self, raw) -> list[dict]:
        """Normalize DuckDuckGo results to a consistent dict format."""
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Failed to parse DuckDuckGo results as JSON")
                return []

        if not isinstance(raw, list):
            return []

        return [
            {
                "title":   r.get("title", ""),
                "link":    r.get("link", ""),
                "snippet": r.get("snippet", ""),
            }
            for r in raw
            if isinstance(r, dict)
        ]

    def search(
        self,
        query: str,
        max_results: int = DEFAULT_MAX_RESULTS,
        geo_focus: Optional[str] = None,
        time_horizon: Optional[str] = None,
    ) -> list[dict]:
        """
        Search the web and return normalized results.

        Args:
            query:        Search query string
            max_results:  Maximum number of results to return
            geo_focus:    Geographic focus (e.g., "Nigeria", "global")
            time_horizon: Time filter (e.g., "last_30_days", "last_5_years")

        Returns:
            List of result dicts with keys: title, link, snippet

        Raises:
            ValueError: If query is empty
        """
        if not query or not query.strip():
            raise ValueError("Query cannot be empty")

        logger.info(f"🔍 Searching: '{query}'")
        enhanced_query = self._build_query(query, geo_focus, time_horizon)

        try:
            if self.google_available:
                logger.debug("Using Google Search API")
                raw = self.google.results(enhanced_query, num_results=max_results)
                return self._parse_google_results(raw)

            logger.debug("Using DuckDuckGo")
            self.duckduckgo.max_results = max_results
            raw = self.duckduckgo._run(enhanced_query)
            return self._parse_duckduckgo_results(raw)

        except Exception as e:
            logger.error(f"❌ Web search failed: {e}")
            raise


# ============================================================================
# SINGLETON + CONVENIENCE FUNCTION
# ============================================================================

@lru_cache(maxsize=1)
def _get_search_tool() -> WebSearchTool:
    """Return a cached singleton instance of WebSearchTool."""
    return WebSearchTool()