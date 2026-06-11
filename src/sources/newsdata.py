"""NewsData.io connector.

Optional source enabled by NEWSDATA_API_KEY, with GOOGLE_NEWS_API_KEY accepted as
an alias because NewsData keys are sometimes described as Google-news style keys.
The connector uses title-only searches to keep precision higher than broad body
searches.
"""

import logging
import os

import pandas as pd
import requests

from src.config import GDELT_TIMEOUT, NEWSDATA_TITLE_QUERIES
from src.sources._common import empty_articles_df, rows_to_df, utc_now

logger = logging.getLogger(__name__)

NEWSDATA_ENDPOINT = "https://newsdata.io/api/1/latest"
NEWSDATA_SIZE_PER_QUERY = 10


def _api_key(explicit: str | None = None) -> str:
    """Read NewsData key, accepting the local alias used in Streamlit secrets."""
    return (
        explicit
        or os.environ.get("NEWSDATA_API_KEY", "")
        or os.environ.get("GOOGLE_NEWS_API_KEY", "")
    ).strip()


def fetch_newsdata(queries: list[str] | None = None, api_key: str | None = None) -> pd.DataFrame:
    """Fetch NewsData latest title-search results; skip cleanly without a key."""
    key = _api_key(api_key)
    if not key:
        return empty_articles_df()
    rows: list[dict] = []
    retrieved = utc_now()
    for query in queries or NEWSDATA_TITLE_QUERIES:
        params = {
            "apikey": key,
            "qInTitle": query,
            "language": "en",
            "size": NEWSDATA_SIZE_PER_QUERY,
        }
        try:
            response = requests.get(NEWSDATA_ENDPOINT, params=params, timeout=GDELT_TIMEOUT)
            if response.status_code == 429:
                logger.warning("NewsData rate limit reached; skipping remaining title queries")
                break
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("NewsData request failed for query %r: %s", query, exc)
            continue
        if payload.get("status") != "success":
            logger.warning("NewsData returned %s for query %r: %s",
                           payload.get("status"), query, payload.get("message"))
            continue
        articles = payload.get("results")
        if not isinstance(articles, list):
            continue
        for item in articles:
            if not isinstance(item, dict):
                continue
            rows.append({
                "title": item.get("title", ""),
                "snippet": item.get("description", "") or item.get("content", ""),
                "url": item.get("link", ""),
                "source_name": item.get("source_name") or item.get("source_id") or "NewsData",
                "published_at": item.get("pubDate"),
                "retrieved_at": retrieved,
                "source_api": "newsdata",
                "query": query,
                "is_sample": False,
            })
    return rows_to_df(rows)
