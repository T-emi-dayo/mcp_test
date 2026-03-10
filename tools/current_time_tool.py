"""
Current Time Tool
Provides accurate real-time date and time information.
Works offline (system clock) or online (optional API fallback).
"""

import requests
from datetime import datetime
from langchain_core.tools import Tool
from core.performance_profiler import profile_tool

@profile_tool("get_current_time_local")
def get_current_time_local() -> str:
    """
    Returns the current system date and time in a readable format.
    Example: "Tuesday, October 21, 2025, 16:04:32"
    """
    return datetime.now().strftime("%A, %B %d, %Y, %H:%M:%S")

@profile_tool("get_current_time_api")
def get_current_time_api() -> str:
    """
    Fetches the current UTC date and time from an online API.
    Falls back to local time if request fails.
    """
    try:
        res = requests.get("http://worldtimeapi.org/api/ip", timeout=5)
        res.raise_for_status()
        data = res.json()
        current_time = data.get("datetime", "")
        timezone = data.get("timezone", "UTC")
        return f"{current_time} ({timezone})"
    except Exception:
        # Fallback to system time if API unavailable
        return get_current_time_local()

# --- LangChain Tool Wrapper ---
get_current_time_tool = Tool(
    name="get_current_time",
    description=(
        "Fetch the current real-world date and time. "
        "Use this tool for all queries involving 'today', 'now', 'current date', "
        "or 'current time' instead of model memory."
    ),
    func=lambda _: get_current_time_api()
)