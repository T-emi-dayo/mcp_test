"""
web_scrapping_tool.py
=====================
Webpage scraper using trafilatura for content extraction.
"""

from __future__ import annotations

import logging
from typing import Optional

from trafilatura import fetch_url, extract

from schemas.Tool_Result import ToolResult

logger = logging.getLogger(__name__)

_ALLOWED_FORMATS = {"txt", "markdown", "xml"}


def scrape_webpage(url: str, output_format: str = "markdown") -> ToolResult:
    """
    Scrape and extract the main content of a webpage.

    Fetches the page at the given URL and extracts the primary text content
    using trafilatura, stripping boilerplate (ads, nav, footers).

    Args:
        url:
            The full URL of the webpage to scrape (must include scheme).
        output_format:
            Format for the extracted content: "txt", "markdown", or "xml".
            Defaults to "markdown". Invalid values fall back to "markdown".

    Returns:
        ToolResult with the extracted content. On failure, the `error` field
        is set and `content` is None — no exception is raised.
    """
    if output_format not in _ALLOWED_FORMATS:
        logger.warning(
            f"scrape_webpage: invalid output_format '{output_format}', using 'markdown'"
        )
        output_format = "markdown"

    logger.info(f"scrape_webpage | url='{url}' format={output_format}")

    try:
        downloaded = fetch_url(url)
    except Exception as e:
        logger.error(f"scrape_webpage fetch failed for '{url}': {e}")
        return ToolResult(
            title   = url,
            url     = url,
            snippet = "",
            source  = "web_scraping_tool",
            error   = f"Fetch failed: {type(e).__name__}: {e}",
        )

    if downloaded is None:
        logger.warning(f"scrape_webpage: fetch_url returned None for '{url}'")
        return ToolResult(
            title   = url,
            url     = url,
            snippet = "",
            source  = "web_scraping_tool",
            error   = "Could not fetch URL — unreachable or blocked",
        )

    try:
        content: Optional[str] = extract(
            filecontent   = downloaded,
            output_format = output_format,
        )
    except Exception as e:
        logger.error(f"scrape_webpage extraction failed for '{url}': {e}")
        return ToolResult(
            title   = url,
            url     = url,
            snippet = "",
            source  = "web_scraping_tool",
            error   = f"Extraction failed: {type(e).__name__}: {e}",
        )

    if not content:
        logger.warning(f"scrape_webpage: no content extracted from '{url}'")
        return ToolResult(
            title   = url,
            url     = url,
            snippet = "",
            source  = "web_scraping_tool",
            error   = "No content could be extracted from the page",
        )

    snippet = content[:300].replace("\n", " ").strip()

    return ToolResult(
        title    = url,
        url      = url,
        snippet  = snippet,
        source   = "web_scraping_tool",
        content  = content,
        metadata = {"output_format": output_format},
    )
