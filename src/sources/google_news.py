"""Google News RSS connector (fallback source).

Links are news.google.com redirect URLs, so the article's real domain is NOT
recoverable — `entry.source.title` is kept as source_name and Phase 5 matches
credibility tiers by name for these rows. Snippets come from `entry.summary`,
which contains HTML that must be stripped.
"""

import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests

from src.config import GDELT_TIMEOUT
from src.sources._common import empty_articles_df, rows_to_df, utc_now

logger = logging.getLogger(__name__)

_RSS_URL = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", text or "")).strip()


def _parse_struct_time(entry: dict) -> datetime | None:
    """Convert feedparser's published_parsed struct_time to aware UTC datetime."""
    tt = entry.get("published_parsed")
    if not tt:
        return None
    try:
        return datetime(*tt[:6], tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def fetch_google_news(queries: list[str], max_per_query: int = 50) -> pd.DataFrame:
    """Fetch articles from Google News RSS search for each plain-text query.

    Returns a DataFrame with the Article column set; a failing query contributes
    zero rows and logs a warning — never raises.
    """
    rows: list[dict] = []
    for query in queries:
        url = _RSS_URL.format(q=quote_plus(query))
        try:
            resp = requests.get(url, timeout=GDELT_TIMEOUT)
            feed = feedparser.parse(resp.content)
        except (requests.RequestException, Exception) as e:  # feedparser is unpredictable
            logger.warning("Google News RSS failed for query %r: %s", query, e)
            continue
        retrieved = utc_now()
        for entry in feed.entries[:max_per_query]:
            source_name = entry.get("source", {}).get("title") or "Google News"
            rows.append({
                "title": entry.get("title", ""),
                "snippet": _strip_html(entry.get("summary", "")),
                "url": entry.get("link", ""),
                "source_name": source_name,
                "published_at": _parse_struct_time(entry),
                "retrieved_at": retrieved,
                "source_api": "google_news_rss",
                "query": query,
                "is_sample": False,
            })
    if not rows:
        return empty_articles_df()
    return rows_to_df(rows)
