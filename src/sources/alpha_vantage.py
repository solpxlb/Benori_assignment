"""Alpha Vantage News Sentiment connector.

Optional source enabled by ALPHAVANTAGE_API_KEY. It is mainly useful for
finance-market coverage of listed FMCG companies and M&A topics; relevance
scoring still decides whether each row belongs in the newsletter.
"""

import logging
import os
from datetime import datetime, timezone

import pandas as pd
import requests

from src.config import GDELT_TIMEOUT
from src.sources._common import empty_articles_df, rows_to_df, utc_now

logger = logging.getLogger(__name__)

ALPHA_VANTAGE_ENDPOINT = "https://www.alphavantage.co/query"
ALPHA_VANTAGE_LIMIT = 100


def _parse_alpha_time(value: str) -> datetime | None:
    """Parse Alpha Vantage YYYYMMDDTHHMMSS timestamps as UTC."""
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def fetch_alpha_vantage(api_key: str | None = None) -> pd.DataFrame:
    """Fetch M&A-topic news from Alpha Vantage; skip cleanly without a key."""
    key = (api_key or os.environ.get("ALPHAVANTAGE_API_KEY", "")).strip()
    if not key:
        return empty_articles_df()
    params = {
        "function": "NEWS_SENTIMENT",
        "topics": "mergers_and_acquisitions",
        "sort": "LATEST",
        "limit": ALPHA_VANTAGE_LIMIT,
        "apikey": key,
    }
    try:
        response = requests.get(ALPHA_VANTAGE_ENDPOINT, params=params, timeout=GDELT_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning("Alpha Vantage request failed: %s", exc)
        return empty_articles_df()

    feed = payload.get("feed")
    if not isinstance(feed, list):
        note = payload.get("Note") or payload.get("Information") or payload.get("Error Message")
        if note:
            logger.warning("Alpha Vantage returned no feed: %s", note)
        return empty_articles_df()

    retrieved = utc_now()
    rows: list[dict] = []
    for item in feed:
        if not isinstance(item, dict):
            continue
        rows.append({
            "title": item.get("title", ""),
            "snippet": item.get("summary", ""),
            "url": item.get("url", ""),
            "source_name": item.get("source", "Alpha Vantage"),
            "published_at": _parse_alpha_time(item.get("time_published", "")),
            "retrieved_at": retrieved,
            "source_api": "alpha_vantage",
            "query": "topics:mergers_and_acquisitions",
            "is_sample": False,
        })
    return rows_to_df(rows)
