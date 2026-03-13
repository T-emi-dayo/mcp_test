"""
tools/time_tool.py

Plain function. No base class. No infrastructure.
Registered in main.py with server.register().
"""

import requests


def get_current_time() -> str:
    """
    Fetch the current date, time, and timezone from worldtimeapi.org.

    Args:
        None

    Returns:
        On success: datetime string with timezone,
                    e.g. "2026-03-12T14:33:21.123456+00:00 (UTC)"
        On error:   string beginning with "ERROR ["
    """
    response = requests.get(
        "http://worldtimeapi.org/api/ip",
        timeout=5,
    )
    response.raise_for_status()
    data = response.json()
    return f"{data.get('datetime', '')} ({data.get('timezone', 'UTC')})"
    
    