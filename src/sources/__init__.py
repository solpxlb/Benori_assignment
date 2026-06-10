"""Source connectors: GDELT (primary), Google News RSS (fallback), PR wires (corroboration)."""

import logging
import uuid

import pandas as pd

from src.config import GDELT_QUERIES, RSS_QUERIES
from src.sources._common import empty_articles_df
from src.sources.gdelt import fetch_gdelt
from src.sources.google_news import fetch_google_news
from src.sources.rss_feeds import fetch_rss_feeds

logger = logging.getLogger(__name__)

__all__ = ["fetch_all", "fetch_gdelt", "fetch_google_news", "fetch_rss_feeds"]


def fetch_all(timespan: str = "7d") -> pd.DataFrame:
    """Run all connectors and return one combined, schema-aligned DataFrame.

    Assigns each row a 12-char article_id and drops rows missing a title or URL.
    Any connector failing entirely contributes zero rows — never raises.
    """
    frames = [
        fetch_gdelt(GDELT_QUERIES, timespan=timespan),
        fetch_google_news(RSS_QUERIES),
        fetch_rss_feeds(),
    ]
    df = pd.concat(frames, ignore_index=True)
    if df.empty:
        return empty_articles_df()
    df["article_id"] = [uuid.uuid4().hex[:12] for _ in range(len(df))]
    df = df[(df["title"].str.strip() != "") & (df["url"].str.strip() != "")]
    return df.reset_index(drop=True)
