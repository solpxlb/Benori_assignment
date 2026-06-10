"""PR-wire RSS connector (optional corroboration source).

Built only for feeds the Phase 0 probe confirmed alive (PR Newswire consumer
category, GlobeNewswire M&A and consumer-products feeds — see config.PR_WIRE_FEEDS).
These rows are official/promotional sources: tier 3 by design, valuable for
corroboration (+official+independent bonus in Phase 5), not standalone evidence.
"""

import logging

import feedparser
import pandas as pd
import requests

from src.config import GDELT_TIMEOUT, PR_WIRE_FEEDS
from src.sources._common import empty_articles_df, rows_to_df, utc_now
from src.sources.google_news import _strip_html, _parse_struct_time

logger = logging.getLogger(__name__)


def fetch_rss_feeds(feeds: list[dict] | None = None) -> pd.DataFrame:
    """Fetch entries from PR-wire RSS feeds; any failing feed is skipped silently.

    `feeds` is a list of {"url", "source_label"} dicts (defaults to config.PR_WIRE_FEEDS).
    Returns a DataFrame with the Article column set; never raises.
    """
    if feeds is None:
        feeds = PR_WIRE_FEEDS
    rows: list[dict] = []
    for feed_cfg in feeds:
        url, label = feed_cfg["url"], feed_cfg["source_label"]
        try:
            resp = requests.get(url, timeout=GDELT_TIMEOUT,
                                headers={"User-Agent": "Mozilla/5.0"})
            feed = feedparser.parse(resp.content)
        except (requests.RequestException, Exception) as e:
            logger.warning("PR-wire feed %s failed: %s", label, e)
            continue
        retrieved = utc_now()
        for entry in feed.entries:
            rows.append({
                "title": entry.get("title", ""),
                "snippet": _strip_html(entry.get("summary", "")),
                "url": entry.get("link", ""),
                "source_name": label,
                "published_at": _parse_struct_time(entry),
                "retrieved_at": retrieved,
                "source_api": "pr_wire_rss",
                "query": f"feed:{label}",
                "is_sample": False,
            })
    if not rows:
        return empty_articles_df()
    return rows_to_df(rows)
