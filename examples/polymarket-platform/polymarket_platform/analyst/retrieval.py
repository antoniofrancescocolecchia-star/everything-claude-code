"""
Evidence retrieval abstraction for Layer 3.

RetrievalProvider ABC defines the interface.
AnthropicWebSearchProvider is the production implementation.
MockRetrievalProvider is used in tests.

Cost: $0.01 per search call (Anthropic web search fee).
      Token costs for the retrieval call are tracked separately.
"""
from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from polymarket_platform.analyst.signals import EvidenceRecord

log = logging.getLogger(__name__)

SEARCH_COST_USD = 0.01  # Per Anthropic web search API call

# Domains we prefer (official, reputable news, government)
_PREFERRED_DOMAINS: frozenset[str] = frozenset(
    [
        "reuters.com",
        "apnews.com",
        "bbc.com",
        "bbc.co.uk",
        "ft.com",
        "bloomberg.com",
        "wsj.com",
        "economist.com",
        "theguardian.com",
        "nytimes.com",
        "washingtonpost.com",
        "politico.com",
        "cnn.com",
        "nbcnews.com",
        "cbsnews.com",
        "abcnews.go.com",
        "npr.org",
        "who.int",
        "cdc.gov",
    ]
)

# Domains we explicitly reject as primary evidence
_BLOCKED_DOMAINS: frozenset[str] = frozenset(
    [
        "twitter.com",
        "x.com",
        "reddit.com",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "youtube.com",
        "t.me",
        "discord.com",
        "discord.gg",
    ]
)


@dataclass
class SearchResult:
    """A single retrieved evidence source."""

    url: str
    title: str
    snippet: str
    published_at: str | None
    age_minutes: float | None
    domain: str

    def to_evidence_record(self) -> EvidenceRecord:
        return EvidenceRecord(
            url=self.url,
            title=self.title,
            snippet=self.snippet,
            published_at=self.published_at,
            age_minutes=self.age_minutes,
            domain=self.domain,
        )


class RetrievalProvider(ABC):
    @abstractmethod
    async def search(
        self, query: str, max_results: int = 5
    ) -> list[SearchResult]: ...

    @abstractmethod
    def cost_per_search_usd(self) -> float: ...


class AnthropicWebSearchProvider(RetrievalProvider):
    """
    Uses Anthropic Messages API with the web_search_20250305 built-in tool.

    Flow:
      1. Send a request with web_search enabled and a list_sources tool.
      2. System prompt instructs the model to search first, then call
         list_sources with structured source data.
      3. Parse the list_sources tool_use input from the response.
      4. Return SearchResult list, filtering blocked domains.

    If list_sources is not called (model chose not to search), returns [].
    Token costs for the retrieval call are returned in the token_counts tuple.
    """

    # Tool schema for structured source reporting
    _LIST_SOURCES_TOOL: dict = {
        "name": "list_sources",
        "description": (
            "Report the sources found via web search. "
            "Call this after searching to provide structured source data."
        ),
        "input_schema": {
            "type": "object",
            "required": ["sources"],
            "properties": {
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["url", "title", "snippet"],
                        "properties": {
                            "url": {"type": "string"},
                            "title": {"type": "string"},
                            "snippet": {"type": "string"},
                            "published_at": {"type": "string"},
                            "domain": {"type": "string"},
                        },
                    },
                }
            },
        },
    }

    _SYSTEM_PROMPT = (
        "You are a research assistant. "
        "Search for recent, reliable information about the given topic using the "
        "web_search tool. Prefer official sources, news agencies, and government data. "
        "After searching, call list_sources with the structured data from your findings. "
        "Focus on sources published in the last 48 hours. "
        "Do not include social media, forums, or personal blogs."
    )

    def __init__(
        self,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._client = httpx.AsyncClient(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "web-search-2025-03-05",
                "content-type": "application/json",
            },
            timeout=45.0,
        )

    def cost_per_search_usd(self) -> float:
        return SEARCH_COST_USD

    async def search(
        self, query: str, max_results: int = 5
    ) -> tuple[list[SearchResult], int, int]:  # type: ignore[override]
        """
        Returns (results, input_tokens, output_tokens).
        Callers should use this signature when tracking costs.
        The ABC signature returns only list[SearchResult] for simpler mocking.
        """
        payload = {
            "model": self._model,
            "max_tokens": 1024,
            "system": self._SYSTEM_PROMPT,
            "tools": [
                {"type": "web_search_20250305", "name": "web_search"},
                self._LIST_SOURCES_TOOL,
            ],
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Search for recent news and information relevant to: "
                        f"{query}\n\n"
                        f"Return up to {max_results} sources via list_sources."
                    ),
                }
            ],
        }
        try:
            resp = await self._client.post("/v1/messages", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            log.warning("Retrieval search HTTP error: %s", exc)
            return [], 0, 0
        except Exception as exc:
            log.warning("Retrieval search error: %s", exc)
            return [], 0, 0

        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)

        results = self._parse_list_sources(data, max_results)
        return results, input_tokens, output_tokens

    def _parse_list_sources(
        self, data: dict, max_results: int
    ) -> list[SearchResult]:
        """Extract SearchResult objects from a list_sources tool_use call."""
        for block in data.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "list_sources":
                raw_sources = block.get("input", {}).get("sources", [])
                return self._parse_raw_sources(raw_sources, max_results)
        return []

    def _parse_raw_sources(
        self, raw_sources: list[dict], max_results: int
    ) -> list[SearchResult]:
        results: list[SearchResult] = []
        now = time.time()
        for src in raw_sources:
            url = src.get("url", "")
            if not url:
                continue
            domain = _extract_domain(url)
            if domain in _BLOCKED_DOMAINS:
                continue
            age_minutes = _parse_age_minutes(src.get("published_at"), now)
            results.append(
                SearchResult(
                    url=url,
                    title=src.get("title", ""),
                    snippet=src.get("snippet", ""),
                    published_at=src.get("published_at"),
                    age_minutes=age_minutes,
                    domain=domain,
                )
            )
            if len(results) >= max_results:
                break
        return results

    async def close(self) -> None:
        await self._client.aclose()


class MockRetrievalProvider(RetrievalProvider):
    """
    Test double. Returns preset results for any query.
    Optionally raises an exception to test error paths.
    """

    def __init__(
        self,
        results: list[SearchResult] | None = None,
        raise_on_search: Exception | None = None,
    ) -> None:
        self._results = results or []
        self._raise = raise_on_search
        self.search_calls: list[str] = []

    def cost_per_search_usd(self) -> float:
        return SEARCH_COST_USD

    async def search(
        self, query: str, max_results: int = 5
    ) -> list[SearchResult]:
        self.search_calls.append(query)
        if self._raise is not None:
            raise self._raise
        return self._results[:max_results]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_domain(url: str) -> str:
    """Extract the registered domain from a URL."""
    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1).lower() if match else ""


def _parse_age_minutes(published_at: str | None, now: float) -> float | None:
    """Parse ISO 8601 timestamp to age in minutes. Returns None if unparseable."""
    if not published_at:
        return None
    from datetime import datetime

    try:
        if "T" in published_at:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            return (now - dt.timestamp()) / 60.0
    except (ValueError, OverflowError):
        pass
    return None
