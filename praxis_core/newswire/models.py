from __future__ import annotations

from pydantic import BaseModel, Field


class PressRelease(BaseModel):
    release_id: str
    title: str
    url: str
    published_at: str
    source: str
    ticker: str = ""
    exchange: str = ""


class FetchedRelease(BaseModel):
    text: str
    metadata: dict[str, str] = Field(default_factory=dict)
