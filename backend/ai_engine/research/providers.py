"""Web search provider integrations for the research agent.

Each provider calls the vendor's documented public API with the configured key. No scraping is
performed and no authentication is bypassed. When no provider is configured the research agent
reports "No verified information found" rather than inventing results.
"""
from __future__ import annotations

import re
import time
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


NOT_CONFIGURED = "No verified external research available: provider not configured."


class SearchProvider:
    name = "none"

    def search(self, query: str, max_results: int = 6) -> list[SearchResult]:
        raise SearchNotConfigured(NOT_CONFIGURED)


class NullSearch(SearchProvider):
    pass


class _HTTPProvider(SearchProvider):
    """Shared HTTP behaviour: bounded timeouts, one retry on timeouts/5xx (never on 429), strict JSON parsing."""

    retries = 1

    def __init__(self, api_key: str, client: httpx.Client | None = None):
        if not api_key:
            raise SearchNotConfigured(f"{NOT_CONFIGURED} ({self.name} API key missing)")
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0))

    def _send(self, method: str, url: str, **kw) -> dict:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.client.request(method, url, **kw)
            except httpx.TimeoutException as exc:
                last = SearchError(f"{self.name} request timed out.")
            except httpx.HTTPError as exc:
                last = SearchError(f"{self.name} request failed: {type(exc).__name__}.")
            else:
                if resp.status_code == 429:
                    raise SearchError(f"{self.name} rate limit reached.", rate_limited=True)
                if resp.status_code in (401, 403):
                    raise SearchError(f"{self.name} rejected the API key (HTTP {resp.status_code}).")
                if resp.status_code >= 500:
                    last = SearchError(f"{self.name} search failed (HTTP {resp.status_code}).")
                elif resp.status_code >= 400:
                    if _is_auth_error(resp):  # e.g. Brave answers an invalid token with 422 SUBSCRIPTION_TOKEN_INVALID
                        raise SearchError(f"{self.name} rejected the API key (HTTP {resp.status_code}).")
                    raise SearchError(f"{self.name} search failed (HTTP {resp.status_code}).")
                else:
                    try:
                        data = resp.json()
                    except ValueError as exc:
                        raise SearchError(f"{self.name} returned an invalid (non-JSON) response.") from exc
                    if not isinstance(data, dict):
                        raise SearchError(f"{self.name} returned an unexpected response shape.")
                    return data
            if attempt < self.retries:
                time.sleep(0.5 * (attempt + 1))
        raise last  # type: ignore[misc]

    def _check(self, resp: httpx.Response):  # kept for backwards compatibility
        if resp.status_code == 429:
            raise SearchError(f"{self.name} rate limit reached.", rate_limited=True)
        if resp.status_code in (401, 403):
            raise SearchError(f"{self.name} rejected the API key (HTTP {resp.status_code}).")
        if resp.status_code >= 400:
            raise SearchError(f"{self.name} search failed (HTTP {resp.status_code}).")


def _is_auth_error(resp: httpx.Response) -> bool:
    try:
        err = resp.json().get("error")
    except (ValueError, AttributeError):
        return False
    if isinstance(err, dict):
        code = str(err.get("code", "")).upper()
        return "TOKEN" in code or "AUTH" in code or (err.get("meta") or {}).get("component") == "authentication"
    return isinstance(err, str) and "api key" in err.lower()


def _valid_url(u) -> bool:
    return isinstance(u, str) and urlparse(u).scheme in ("http", "https") and bool(urlparse(u).netloc)


def _items(data: dict, *path: str) -> list[dict]:
    cur = data
    for p in path:
        cur = cur.get(p, {}) if isinstance(cur, dict) else {}
    return [r for r in (cur if isinstance(cur, list) else []) if isinstance(r, dict)]


class TavilySearch(_HTTPProvider):
    name = "tavily"

    def search(self, query: str, max_results: int = 6) -> list[SearchResult]:
        data = self._send("POST", "https://api.tavily.com/search", headers={"Authorization": f"Bearer {self.api_key}"},
                          json={"query": query, "max_results": max_results, "search_depth": "basic", "include_answer": False})
        out = []
        for r in _items(data, "results"):
            if not _valid_url(r.get("url")):
                continue
            out.append(SearchResult(title=str(r.get("title") or r["url"]), url=r["url"], snippet=str(r.get("content") or ""),
                                    source_name=_domain(r["url"]), published=parse_published(r.get("published_date"))))
        return out


class BraveSearch(_HTTPProvider):
    name = "brave"

    def search(self, query: str, max_results: int = 6) -> list[SearchResult]:
        data = self._send("GET", "https://api.search.brave.com/res/v1/web/search",
                          params={"q": query, "count": max_results, "country": "IN"},
                          headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"})
        out = []
        for r in _items(data, "web", "results"):
            if not _valid_url(r.get("url")):
                continue
            out.append(SearchResult(title=re.sub(r"<[^>]+>", "", str(r.get("title") or r["url"])), url=r["url"],
                                    snippet=re.sub(r"<[^>]+>", "", str(r.get("description") or "")),
                                    source_name=(r.get("profile") or {}).get("name") or _domain(r["url"]),
                                    published=parse_published(r.get("page_age") or r.get("age"))))
        return out


class SerpAPISearch(_HTTPProvider):
    name = "serpapi"

    def search(self, query: str, max_results: int = 6) -> list[SearchResult]:
        data = self._send("GET", "https://serpapi.com/search.json",
                          params={"engine": "google", "q": query, "num": max_results, "gl": "in", "hl": "en", "api_key": self.api_key})
        if data.get("error"):
            raise SearchError(f"serpapi error: {str(data['error'])[:120]}")
        out = []
        for r in _items(data, "organic_results")[:max_results]:
            if not _valid_url(r.get("link")):
                continue
            out.append(SearchResult(title=str(r.get("title") or r["link"]), url=r["link"], snippet=str(r.get("snippet") or ""),
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
