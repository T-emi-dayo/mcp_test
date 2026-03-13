"""
Current Time Tool
Provides accurate real-time date and time information.
Works offline (system clock) or online (optional API fallback).
"""

import requests
from datetime import datetime
from langchain_core.tools import Tool

from tools.base import BaseTool


class FetchCurrentTimeTool(BaseTool):
    """
    A tool to fetch the current date and time.
    Uses an online API for accuracy, with a local fallback.
    """

    def run(self) -> str:
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
            return self.get_current_time_local()
        
    def get_current_time_local(self) -> str:
        """
        Returns the current system date and time in a readable format.
        Example: "Tuesday, October 21, 2025, 16:04:32"
        """
        return datetime.now().strftime("%A, %B %d, %Y, %H:%M:%S")
    
    