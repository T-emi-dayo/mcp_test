from typing import Optional
from pydantic import BaseModel, ConfigDict, Field


class ToolResult(BaseModel):
    """Result from a tool (search, API, scraping, etc.)."""

    model_config = ConfigDict(exclude_none=True)

    title: str
    url: str
    snippet: str
    source: str
    date: Optional[str] = None
    content: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    error: Optional[str] = None
