import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.platform.config.settings import settings


class GNewsError(RuntimeError):
    pass


@dataclass(frozen=True)
class GNewsArticle:
    """
    Minimal representation of a GNews article payload.
    We keep `raw` so normalisation can evolve without changing ingestion.
    """
    title: str | None
    description: str | None
    content: str | None
    url: str
    published_at: str | None
    source_name: str | None
    raw: dict[str, Any]


class GNewsClient:
    """
    Thin HTTP client for GNews.
    Uses stdlib urllib to avoid adding dependencies.
    """

    BASE_URL = "https://gnews.io/api/v4"

    def __init__(self, api_key: str | None = None, timeout_seconds: int = 15):
        self.api_key = api_key or settings.gnews_api_key
        self.timeout_seconds = timeout_seconds

    def _require_key(self) -> str:
        if not self.api_key:
            raise GNewsError(
                "GNews API key missing. Set GNEWS_API_KEY in backend/.env"
            )
        return self.api_key

    def search(
        self,
        query: str,
        *,
        lang: str = "en",
        country: str = "gb",
        max_results: int = 10,
        sort_by: str = "publishedAt",
    ) -> list[GNewsArticle]:
        """
        GNews search endpoint.
        """
        key = self._require_key()

        params = {
            "q": query,
            "lang": lang,
            "country": country,
            "max": max_results,
            "sortby": sort_by,
            "token": key,
        }

        url = f"{self.BASE_URL}/search?{urlencode(params)}"
        payload = self._get_json(url)

        articles = payload.get("articles") or []
        out: list[GNewsArticle] = []

        for a in articles:
            url_val = a.get("url")
            if not url_val:
                # Skip malformed results
                continue

            source = a.get("source") or {}
            out.append(
                GNewsArticle(
                    title=a.get("title"),
                    description=a.get("description"),
                    content=a.get("content"),
                    url=url_val,
                    published_at=a.get("publishedAt"),
                    source_name=source.get("name"),
                    raw=a,
                )
            )

        return out

    def top_headlines(
        self,
        *,
        lang: str = "en",
        country: str = "gb",
        max_results: int = 10,
        topic: str | None = None,
    ) -> list[GNewsArticle]:
        """
        GNews top-headlines endpoint.
        """
        key = self._require_key()

        params = {
            "lang": lang,
            "country": country,
            "max": max_results,
            "token": key,
        }
        if topic:
            params["topic"] = topic

        url = f"{self.BASE_URL}/top-headlines?{urlencode(params)}"
        payload = self._get_json(url)

        articles = payload.get("articles") or []
        out: list[GNewsArticle] = []

        for a in articles:
            url_val = a.get("url")
            if not url_val:
                continue

            source = a.get("source") or {}
            out.append(
                GNewsArticle(
                    title=a.get("title"),
                    description=a.get("description"),
                    content=a.get("content"),
                    url=url_val,
                    published_at=a.get("publishedAt"),
                    source_name=source.get("name"),
                    raw=a,
                )
            )

        return out

    def _get_json(self, url: str) -> dict[str, Any]:
        req = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "HeadlinesAPI/1.0",
            },
            method="GET",
        )

        try:
            with urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
        except Exception as e:
            raise GNewsError(f"GNews request failed: {e}") from e

        # GNews tends to return error payloads as JSON too
        if isinstance(data, dict) and data.get("errors"):
            raise GNewsError(f"GNews API error: {data.get('errors')}")

        # Some APIs use {"error": "..."}
        if isinstance(data, dict) and data.get("error"):
            raise GNewsError(f"GNews API error: {data.get('error')}")

        return data


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()