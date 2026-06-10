"""Sample-data loader.

The CSV stores `days_ago` offsets instead of absolute dates; the loader computes
published_at = now - days_ago so the demo never looks stale. Every row is marked
is_sample=True and the UI/newsletter must surface that label.
"""

import uuid
from datetime import timedelta
from pathlib import Path

import pandas as pd

from src.sources._common import rows_to_df, utc_now

SAMPLE_CSV = Path(__file__).resolve().parent.parent / "data" / "sample_articles.csv"


def load_sample(csv_path: Path = SAMPLE_CSV) -> pd.DataFrame:
    """Load the synthetic sample articles with dates shifted relative to now.

    Returns a schema-aligned DataFrame (same columns as live ingestion) with
    is_sample=True, source_api="sample", fresh article_ids, and published_at
    computed as now - days_ago (small hour offsets keep timestamps distinct).
    """
    raw = pd.read_csv(csv_path)
    now = utc_now()
    rows: list[dict] = []
    for i, r in raw.iterrows():
        rows.append({
            "article_id": uuid.uuid4().hex[:12],
            "title": r["title"],
            "snippet": r["snippet"],
            "url": r["url"],
            "source_name": r["source_name"],
            "published_at": now - timedelta(days=int(r["days_ago"]), hours=i % 12),
            "retrieved_at": now,
            "source_api": "sample",
            "query": r["query"],
            "is_sample": True,
        })
    return rows_to_df(rows)
