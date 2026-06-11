"""Source connectors for live public news ingestion."""

import logging
import os
import uuid

import pandas as pd

from src.config import GDELT_QUERIES, RSS_QUERIES
from src.sources._common import empty_articles_df, filter_by_timespan
from src.sources.alpha_vantage import fetch_alpha_vantage
from src.sources.gdelt import fetch_gdelt
from src.sources.google_news import fetch_google_news
from src.sources.marketaux import fetch_marketaux
from src.sources.newsdata import fetch_newsdata
from src.sources.newsapi import fetch_newsapi
from src.sources.rss_feeds import fetch_rss_feeds

logger = logging.getLogger(__name__)

__all__ = [
    "fetch_all",
    "fetch_alpha_vantage",
    "fetch_gdelt",
    "fetch_google_news",
    "fetch_marketaux",
    "fetch_newsdata",
    "fetch_newsapi",
    "fetch_rss_feeds",
]


def _keyed_sources_available() -> bool:
    """True when any optional keyed news API is configured."""
    return any(
        os.environ.get(name, "").strip()
        for name in [
            "ALPHAVANTAGE_API_KEY",
            "MARKETAUX_API_TOKEN",
            "NEWSAPI_API_KEY",
            "NEWSDATA_API_KEY",
            "GOOGLE_NEWS_API_KEY",
        ]
    )


def _gdelt_enabled() -> bool:
    """Run GDELT unless keyed APIs are available and no override is set."""
    override = os.environ.get("DEALLENS_ENABLE_GDELT", "").strip().lower()
    if override in {"1", "true", "yes"}:
        return True
    if override in {"0", "false", "no"}:
        return False
    return not _keyed_sources_available()


def fetch_all(timespan: str = "7d") -> pd.DataFrame:
    """Run all connectors and return one combined, schema-aligned DataFrame.

    Assigns each row a 12-char article_id and drops rows missing a title or URL.
    Any connector failing entirely contributes zero rows — never raises.
    """
    frames = []
    connectors = [
        ("google_news_rss", lambda: fetch_google_news(RSS_QUERIES, timespan=timespan)),
        ("pr_wire_rss", lambda: fetch_rss_feeds(timespan=timespan)),
        ("alpha_vantage", fetch_alpha_vantage),
        ("marketaux", fetch_marketaux),
        ("newsdata", fetch_newsdata),
        ("newsapi", lambda: fetch_newsapi(timespan=timespan)),
    ]
    if _gdelt_enabled():
        connectors.insert(0, ("gdelt", lambda: fetch_gdelt(GDELT_QUERIES, timespan=timespan)))
    else:
        logger.info("Skipping GDELT because keyed live-news APIs are configured")
    for name, fetcher in connectors:
        try:
            frame = fetcher()
        except Exception as exc:
            logger.warning("%s connector failed: %s", name, exc)
            frame = empty_articles_df()
        logger.info("%s connector returned %s raw row(s)", name, len(frame))
        frames.append(frame)
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return empty_articles_df()
    df = filter_by_timespan(df, timespan)
    if df.empty:
        return empty_articles_df()
    df["article_id"] = [uuid.uuid4().hex[:12] for _ in range(len(df))]
    df = df[(df["title"].str.strip() != "") & (df["url"].str.strip() != "")]
    return df.reset_index(drop=True)
