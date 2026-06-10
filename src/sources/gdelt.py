"""GDELT DOC 2.0 connector (primary source).

Phase 0/2 findings baked in: error pages arrive with HTTP 200 and text/html, so the
JSON parse is wrapped rather than trusting status codes; rate-limit enforcement is
erratic (429s observed even at 8-10s spacing under sustained use), so we sleep
GDELT_SLEEP_BETWEEN between queries and treat partial results as normal; artlist
mode has no snippet field.
"""

import logging
import time
from datetime import datetime, timezone

import pandas as pd
import requests

from src.config import GDELT_ENDPOINT, GDELT_TIMEOUT, GDELT_SLEEP_BETWEEN, MAX_RECORDS_PER_QUERY
from src.sources._common import empty_articles_df, rows_to_df, utc_now

logger = logging.getLogger(__name__)


def parse_seendate(seendate: str) -> datetime | None:
    """Parse GDELT's YYYYMMDDTHHMMSSZ seendate into an aware UTC datetime."""
    try:
        return datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def fetch_gdelt(queries: list[str], timespan: str = "7d",
                max_records: int = MAX_RECORDS_PER_QUERY) -> pd.DataFrame:
    """Fetch articles from GDELT DOC 2.0 for each query; failures yield empty results.

    Returns a DataFrame with the Article column set; never raises on network or
    parse errors — each failing query contributes zero rows and logs a warning.
    """
    rows: list[dict] = []
    # Pacing only matters for requests that reached the server; after a connection
    # failure (offline, DNS, timeout) skip the sleep so the zero-internet sample-mode
    # path fails fast instead of burning the full sleep chain.
    last_request_reached_server = False
    for query in queries:
        if last_request_reached_server:
            time.sleep(GDELT_SLEEP_BETWEEN)
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": max_records,
            "timespan": timespan,
            "sort": "datedesc",
        }
        try:
            resp = requests.get(GDELT_ENDPOINT, params=params, timeout=GDELT_TIMEOUT)
        except requests.RequestException as e:
            logger.warning("GDELT request failed for query %r: %s", query, e)
            last_request_reached_server = False
            continue
        last_request_reached_server = True
        if "json" not in (resp.headers.get("content-type") or ""):
            logger.warning("GDELT non-JSON response (HTTP %s) for query %r: %r",
                           resp.status_code, query, resp.text[:120])
            continue
        try:
            articles = resp.json().get("articles", [])
        except ValueError as e:
            logger.warning("GDELT JSON parse failed for query %r: %s", query, e)
            continue
        retrieved = utc_now()
        for a in articles:
            rows.append({
                "title": a.get("title", ""),
                "snippet": "",  # artlist mode has no snippet (Phase 0 finding)
                "url": a.get("url", ""),
                "source_name": a.get("domain", ""),
                "domain": a.get("domain", ""),
                "published_at": parse_seendate(a.get("seendate", "")),
                "retrieved_at": retrieved,
                "source_api": "gdelt",
                "query": query,
                "is_sample": False,
            })
    if not rows:
        return empty_articles_df()
    return rows_to_df(rows)
