"""
Current Time Tool
Provides accurate real-time date and time information.
Works offline (system clock) or online (optional API fallback).
"""

import logging

import requests
from datetime import datetime

logger = logging.getLogger(__name__)


def get_current_time_local() -> str:
    """
    Returns the current system date and time in a readable format.
    Example: "Tuesday, October 21, 2025, 16:04:32"
    """
    return datetime.now().strftime("%A, %B %d, %Y, %H:%M:%S")


def get_current_time() -> str:
    """
    Fetches the current UTC date and time from worldtimeapi.org.
    Falls back to the local system clock if the API is unavailable.
    """
    try:
        res = requests.get("http://worldtimeapi.org/api/ip", timeout=5)
        res.raise_for_status()
        data = res.json()
        current_time = data.get("datetime", "")
        timezone = data.get("timezone", "UTC")
        return f"{current_time} ({timezone})"
    except Exception as e:
        logger.warning(f"worldtimeapi.org unavailable ({type(e).__name__}), using system clock")
        return get_current_time_local()