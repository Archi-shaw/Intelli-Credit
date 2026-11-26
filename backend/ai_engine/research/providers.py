"""Web search provider integrations for the research agent.

Each provider calls the vendor's documented public API with the configured key. No scraping is
performed and no authentication is bypassed. When no provider is configured the research agent
reports "No verified information found" rather than inventing results.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx


class SearchNotConfigured(Exception):
    pass


class SearchError(Exception):
    def __init__(self, message: str, rate_limited: bool = False):
        super().__init__(message)
        self.rate_limited = rate_limited


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source_name: str
    published: date | None


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def parse_published(value) -> date | None:
    if not value:
        return None
    s = str(value).strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.match(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", s, re.I)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        days = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        return (datetime.now(timezone.utc) - timedelta(days=days)).date()
    for fmt in ("%b %d, %Y", "%d %b %Y", "%B %d, %Y", "%d %B %Y", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            return datetime.strptime(s[:30], fmt).date()
        except ValueError:
            continue
    return None


class SearchProvider:
    name = "none"

    def search(self, query: str, max_results: int = 6) -> list[SearchResult]:
        raise SearchNotConfigured("No search provider configured. Set SEARCH_PROVIDER and SEARCH_API_KEY.")


class NullSearch(SearchProvider):
    pass


class _HTTPProvider(SearchProvider):
    def __init__(self, api_key: str, client: httpx.Client | None = None):
        if not api_key:
            raise SearchNotConfigured(f"SEARCH_API_KEY is required for the {self.name} provider.")
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=20.0)

    def _check(self, resp: httpx.Response):
        if resp.status_code == 429:
            raise SearchError(f"{self.name} rate limit reached.", rate_limited=True)
        if resp.status_code in (401, 403):
            raise SearchError(f"{self.name} rejected the API key (HTTP {resp.status_code}).")
        if resp.status_code >= 400:
            raise SearchError(f"{self.name} search failed (HTTP {resp.status_code}).")


class TavilySearch(_HTTPProvider):
    name = "tavily"

    def search(self, query: str, max_results: int = 6) -> list[SearchResult]:
        try:
            resp = self.client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"query": query, "max_results": max_results, "search_depth": "basic", "include_answer": False},
            )
        except httpx.HTTPError as exc:
            raise SearchError(f"Tavily request failed: {exc}") from exc
        self._check(resp)
        out = []
        for r in resp.json().get("results", []):
            if not r.get("url"):
                continue
            out.append(SearchResult(title=r.get("title") or r["url"], url=r["url"], snippet=r.get("content") or "",
                                    source_name=_domain(r["url"]), published=parse_published(r.get("published_date"))))
        return out


class BraveSearch(_HTTPProvider):
    name = "brave"

    def search(self, query: str, max_results: int = 6) -> list[SearchResult]:
        try:
            resp = self.client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results, "country": "IN"},
                headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise SearchError(f"Brave request failed: {exc}") from exc
        self._check(resp)
        out = []
        for r in resp.json().get("web", {}).get("results", []):
            if not r.get("url"):
                continue
            out.append(SearchResult(title=re.sub(r"<[^>]+>", "", r.get("title") or r["url"]), url=r["url"],
                                    snippet=re.sub(r"<[^>]+>", "", r.get("description") or ""),
                                    source_name=(r.get("profile") or {}).get("name") or _domain(r["url"]),
                                    published=parse_published(r.get("page_age") or r.get("age"))))
        return out


class SerpAPISearch(_HTTPProvider):
    name = "serpapi"

    def search(self, query: str, max_results: int = 6) -> list[SearchResult]:
        try:
            resp = self.client.get(
                "https://serpapi.com/search.json",
                params={"engine": "google", "q": query, "num": max_results, "gl": "in", "hl": "en", "api_key": self.api_key},
            )
        except httpx.HTTPError as exc:
            raise SearchError(f"SerpAPI request failed: {exc}") from exc
        self._check(resp)
        out = []
        for r in resp.json().get("organic_results", [])[:max_results]:
            if not r.get("link"):
                continue
            out.append(SearchResult(title=r.get("title") or r["link"], url=r["link"], snippet=r.get("snippet") or "",
                                    source_name=r.get("source") or _domain(r["link"]), published=parse_published(r.get("date"))))
        return out


def get_search_provider(name: str, api_key: str, client: httpx.Client | None = None) -> SearchProvider:
    if name == "tavily":
        return TavilySearch(api_key, client)
    if name == "brave":
        return BraveSearch(api_key, client)
    if name == "serpapi":
        return SerpAPISearch(api_key, client)
    return NullSearch()


OFFICIAL = ("gov.in", "nic.in", "rbi.org.in", "sebi.gov.in", "mca.gov.in", "ecourts.gov.in", "nclt.gov.in", "bseindia.com",
            "nseindia.com", "ibbi.gov.in")
RATING_AGENCIES = ("crisil.com", "icra.in", "careratings.com", "careedge.in", "indiaratings.co.in", "acuite.in",
                   "brickworkratings.com", "infomerics.com")
REPUTED_MEDIA = ("economictimes.indiatimes.com", "livemint.com", "business-standard.com", "thehindubusinessline.com",
                 "financialexpress.com", "moneycontrol.com", "reuters.com", "bloomberg.com", "thehindu.com",
                 "indianexpress.com", "ndtvprofit.com", "cnbctv18.com", "hindustantimes.com")


def source_tier(url: str | None) -> str:
    if not url:
        return "general"
    d = _domain(url)
    if any(d == x or d.endswith("." + x) or d.endswith(x) for x in OFFICIAL):
        return "official"
    if any(d.endswith(x) for x in RATING_AGENCIES):
        return "rating_agency"
    if any(d.endswith(x) for x in REPUTED_MEDIA):
        return "reputed_media"
    return "general"
