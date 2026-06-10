"""Shared helpers for source connectors: schema-aligned empty frames and row building."""

import dataclasses
import logging
from datetime import datetime, timezone

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
