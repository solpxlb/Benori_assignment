"""Article schema.

The pipeline operates on a pandas DataFrame whose columns mirror this dataclass;
the dataclass exists to document the schema in one authoritative place.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class Article:
    """One fetched news article, carried through cleaning, dedup, scoring, clustering."""

    article_id: str                       # uuid4 hex[:12], assigned at ingestion
    title: str
    snippet: str                          # "" for GDELT artlist rows (no snippet available)
    url: str                              # as returned by the source
    canonical_url: str = ""               # cleaned URL (Phase 3)
    source_name: str = ""                 # publisher name (Google News: entry.source.title)
    domain: str = ""                      # registered domain via tldextract (Phase 3)
    published_at: datetime | None = None  # timezone-aware UTC
    retrieved_at: datetime | None = None  # timezone-aware UTC, set at fetch time
    source_api: str = ""                  # "gdelt" | "google_news_rss" | "pr_wire_rss" | "sample"
    query: str = ""                       # originating query family/string
    is_sample: bool = False               # True only for synthetic demo rows
    normalized_title: str = ""            # cleaned title for dedup (Phase 3)
    duplicate_of: str = ""                # article_id of the canonical row, if duplicate
    dedupe_status: str = ""               # "canonical" | "duplicate" (Phase 4)
    dedupe_reason: str = ""               # e.g. "exact_url", "fuzzy_title_90" (Phase 4)
    relevance_score: int = 0              # 0-100 (Phase 5)
    relevance_reasons: list[str] = field(default_factory=list)  # reason codes (Phase 5)
    credibility_score: int = 0            # 0-100 (Phase 5)
    credibility_tier: str = ""            # "tier_1".."tier_4" (Phase 5)
    cluster_id: str = ""                  # deal-event cluster id (Phase 6)
    status: str = ""                      # "include" | "watchlist" | "reject" (Phase 5)

    def to_dict(self) -> dict:
        """Return the article as a plain dict (DataFrame-row / JSON friendly)."""
        return asdict(self)
