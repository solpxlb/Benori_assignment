"""Shared helpers for source connectors: schema-aligned empty frames and row building."""

import dataclasses
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd

from src.models import Article

logger = logging.getLogger(__name__)

# Authoritative column order, derived from the Article dataclass.
ARTICLE_COLUMNS: list[str] = [f.name for f in dataclasses.fields(Article)]


def empty_articles_df() -> pd.DataFrame:
    """Return an empty DataFrame with the full Article column set."""
    return pd.DataFrame(columns=ARTICLE_COLUMNS)


def rows_to_df(rows: list[dict]) -> pd.DataFrame:
    """Convert raw row dicts to a schema-aligned DataFrame (missing columns filled)."""
    if not rows:
        return empty_articles_df()
    df = pd.DataFrame(rows)
    for col in ARTICLE_COLUMNS:
        if col not in df.columns:
            df[col] = [[] for _ in range(len(df))] if col == "relevance_reasons" else ""
    return df[ARTICLE_COLUMNS]


def utc_now() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


def timespan_to_timedelta(timespan: str) -> timedelta:
    """Convert app timespan strings like '1d' or '30d' into timedeltas."""
    value = (timespan or "7d").strip().lower()
    try:
        if value.endswith("d"):
            return timedelta(days=int(value[:-1]))
        if value.endswith("h"):
            return timedelta(hours=int(value[:-1]))
    except ValueError:
        logger.warning("Invalid timespan %r; defaulting to 7d", timespan)
    return timedelta(days=7)


def google_when(timespan: str) -> str:
    """Return a Google News RSS `when:` token for the selected timespan."""
    delta = timespan_to_timedelta(timespan)
    hours = int(delta.total_seconds() // 3600)
    if hours < 24:
        return f"{max(hours, 1)}h"
    return f"{max(delta.days, 1)}d"


def filter_by_timespan(
    df: pd.DataFrame,
    timespan: str,
    now: datetime | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """Keep only rows with published_at inside the selected lookback window.

    Rows without a usable date are dropped for live-data correctness: unknown
    dates should not make a "Last 7 days" newsletter or suppress sample fallback.
    A 1-day future tolerance protects against feed clock skew.
    """
    if df.empty or "published_at" not in df:
        return df.copy()
    out = df.copy()
    now_ts = pd.Timestamp(now or utc_now()).tz_convert("UTC")
    published = pd.to_datetime(out["published_at"], utc=True, errors="coerce")
    cutoff = now_ts - timespan_to_timedelta(timespan)
    upper = now_ts + timedelta(days=1)
    mask = published.notna() & (published >= cutoff) & (published <= upper)
    return out.loc[mask].reset_index(drop=True)
