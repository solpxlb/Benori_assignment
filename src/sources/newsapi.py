"""NewsAPI connector.

Optional source enabled by NEWSAPI_API_KEY. NewsAPI free keys are useful for
local/demo testing; users should check their plan terms before production use.
"""

import logging
import os

import pandas as pd
import requests

from src.config import GDELT_TIMEOUT, KEYED_NEWS_QUERIES
from src.sources._common import empty_articles_df, rows_to_df, timespan_to_timedelta, utc_now

logger = logging.getLogger(__name__)

NEWSAPI_ENDPOINT = "https://newsapi.org/v2/everything"
NEWSAPI_PAGE_SIZE = 50


def _newsapi_query(query: str) -> str:
    """Convert a loose query into NewsAPI's Lucene-ish syntax."""
    return query.replace(" OR ", " OR ")


def fetch_newsapi(
    queries: list[str] | None = None,
    timespan: str = "7d",
    api_key: str | None = None,
) -> pd.DataFrame:
    """Fetch NewsAPI everything results; skip cleanly without a key."""
    key = (api_key or os.environ.get("NEWSAPI_API_KEY", "")).strip()
    if not key:
        return empty_articles_df()
    retrieved = utc_now()
    from_date = (retrieved - timespan_to_timedelta(timespan)).date().isoformat()
    rows: list[dict] = []
    for query in queries or KEYED_NEWS_QUERIES:
        params = {
            "q": _newsapi_query(query),
            "searchIn": "title,description",
            "from": from_date,
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": NEWSAPI_PAGE_SIZE,
            "apiKey": key,
        }
        try:
            response = requests.get(NEWSAPI_ENDPOINT, params=params, timeout=GDELT_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("NewsAPI request failed for query %r: %s", query, exc)
            continue
        articles = payload.get("articles")
        if not isinstance(articles, list):
            message = payload.get("message") or payload.get("code") or "unknown response"
            logger.warning("NewsAPI returned no articles for query %r: %s", query, message)
            continue
        for item in articles:
            if not isinstance(item, dict):
                continue
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            rows.append({
                "title": item.get("title", ""),
                "snippet": item.get("description", "") or item.get("content", ""),
                "url": item.get("url", ""),
                "source_name": source.get("name") or "NewsAPI",
                "published_at": item.get("publishedAt"),
                "retrieved_at": retrieved,
                "source_api": "newsapi",
                "query": query,
                "is_sample": False,
            })
    return rows_to_df(rows)
