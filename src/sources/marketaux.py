"""Marketaux connector.

Optional source enabled by MARKETAUX_API_TOKEN. The free plan is constrained, so
queries are intentionally compact and the deterministic relevance layer filters
aggressively after ingestion.
"""

import logging
import os

import pandas as pd
import requests

from src.config import GDELT_TIMEOUT, KEYED_NEWS_QUERIES
from src.sources._common import empty_articles_df, rows_to_df, utc_now

logger = logging.getLogger(__name__)

MARKETAUX_ENDPOINT = "https://api.marketaux.com/v1/news/all"
MARKETAUX_LIMIT_PER_QUERY = 3


def fetch_marketaux(queries: list[str] | None = None, api_token: str | None = None) -> pd.DataFrame:
    """Fetch news from Marketaux; skip cleanly without a token."""
    token = (api_token or os.environ.get("MARKETAUX_API_TOKEN", "")).strip()
    if not token:
        return empty_articles_df()
    rows: list[dict] = []
    retrieved = utc_now()
    for query in queries or KEYED_NEWS_QUERIES:
        params = {
            "api_token": token,
            "search": query,
            "language": "en",
            "limit": MARKETAUX_LIMIT_PER_QUERY,
        }
        try:
            response = requests.get(MARKETAUX_ENDPOINT, params=params, timeout=GDELT_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Marketaux request failed for query %r: %s", query, exc)
            continue
        articles = payload.get("data")
        if not isinstance(articles, list):
            logger.warning("Marketaux returned no data for query %r", query)
            continue
        for item in articles:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            if isinstance(source, dict):
                source_name = source.get("name") or source.get("domain") or "Marketaux"
            else:
                source_name = str(source or "").strip() or "Marketaux"
            rows.append({
                "title": item.get("title", ""),
                "snippet": item.get("description", "") or item.get("snippet", ""),
                "url": item.get("url", ""),
                "source_name": source_name,
                "published_at": item.get("published_at"),
                "retrieved_at": retrieved,
                "source_api": "marketaux",
                "query": query,
                "is_sample": False,
            })
    return rows_to_df(rows)
