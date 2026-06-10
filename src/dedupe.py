"""Deduplication: four passes, transparent reasons, duplicates retained not deleted.

Pass order (each pass only examines rows still canonical):
  1. exact canonical URL                      -> "exact_url"
  2. exact normalized title within 14 days    -> "exact_title_14d"
  3. fuzzy title (token_set_ratio >= 90, 14d) -> "fuzzy_title_90"
  4. TF-IDF cosine >= 0.82 within 14 days     -> "tfidf_similarity_82" (optional pass)

Duplicates are flagged, never dropped: the Transparency tab and exports show them
with reasons. Canonical selection prefers higher credibility tier, then longer
snippet, then newer date.
"""

import functools
from pathlib import Path

import pandas as pd
import yaml
from rapidfuzz import fuzz
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.config import (DEDUPE_FUZZY_THRESHOLD, DEDUPE_TFIDF_THRESHOLD,
                        DEDUPE_DATE_WINDOW_DAYS)

TIERS_YAML = Path(__file__).resolve().parent.parent / "data" / "credibility_tiers.yaml"


@functools.lru_cache(maxsize=1)
def _load_tiers(path: str = str(TIERS_YAML)) -> dict:
    """Load and cache the credibility tiers YAML."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_tier(domain: str) -> int:
    """Lightweight domain -> tier lookup for canonical selection.

    Phase 5 owns the full credibility model (incl. name-based matching for Google
    News rows); this stub only needs enough to rank rows within a dupe group.
    """
    tiers = _load_tiers()
    domain = (domain or "").lower()
    for tier_no in (1, 2, 3):
        if domain in tiers.get(f"tier_{tier_no}", {}).get("domains", []):
            return tier_no
    return tiers.get("default_tier", 4)


def _preference_order(df: pd.DataFrame, indices: list) -> list:
    """Sort row indices best-first: lower tier number, longer snippet, newer date."""
    def key(idx):
        row = df.loc[idx]
        ts = row["published_at"]
        ts_key = ts.timestamp() if pd.notna(ts) else float("-inf")
        return (get_tier(row["domain"]), -len(row["snippet"] or ""), -ts_key)
    return sorted(indices, key=key)


def _within_window(df: pd.DataFrame, idx_a, idx_b) -> bool:
    """True if both rows have dates within DEDUPE_DATE_WINDOW_DAYS of each other."""
    a, b = df.loc[idx_a, "published_at"], df.loc[idx_b, "published_at"]
    if pd.isna(a) or pd.isna(b):
        return False
    return abs((a - b).days) <= DEDUPE_DATE_WINDOW_DAYS


def _mark_group(df: pd.DataFrame, indices: list, reason: str,
                require_window: bool = False) -> None:
    """Mark all but the best row in `indices` as duplicates of the best row."""
    ordered = _preference_order(df, indices)
    canonical = ordered[0]
    for idx in ordered[1:]:
        if require_window and not _within_window(df, canonical, idx):
            continue
        df.loc[idx, "duplicate_of"] = df.loc[canonical, "article_id"]
        df.loc[idx, "dedupe_status"] = "duplicate"
        df.loc[idx, "dedupe_reason"] = reason


def _canonical_indices(df: pd.DataFrame) -> list:
    """Indices of rows still marked canonical."""
    return df.index[df["dedupe_status"] == "canonical"].tolist()


def _greedy_pairwise(df: pd.DataFrame, is_dup, reason: str) -> None:
    """Greedy scan of remaining canonicals in preference order.

    The first row seen in each similarity group becomes (stays) canonical; later
    rows matching it via `is_dup(kept_idx, idx)` are flagged. Plain pairwise is
    O(n^2) but acceptable at <500 articles; at larger scale, block on shared
    title tokens to get O(n*k).
    """
    kept: list = []
    for idx in _preference_order(df, _canonical_indices(df)):
        match = next((k for k in kept
                      if _within_window(df, k, idx) and is_dup(k, idx)), None)
        if match is not None:
            df.loc[idx, "duplicate_of"] = df.loc[match, "article_id"]
            df.loc[idx, "dedupe_status"] = "duplicate"
            df.loc[idx, "dedupe_reason"] = reason
        else:
            kept.append(idx)


def dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Run all four dedup passes; returns a copy with dedupe columns filled.

    Zero rows are deleted: dedupe_status partitions the frame into "canonical"
    and "duplicate", and duplicate_of always points at a canonical row's
    article_id (chains are resolved).
    """
    df = df.copy()
    df["duplicate_of"] = ""
    df["dedupe_status"] = "canonical"
    df["dedupe_reason"] = ""
    if len(df) < 2:
        return df

    # Pass 1: exact canonical URL (no date window — same URL is the same article).
    for _, group in df.groupby("canonical_url"):
        if len(group) > 1 and group["canonical_url"].iloc[0]:
            _mark_group(df, group.index.tolist(), "exact_url")

    # Pass 2: exact normalized title within the date window.
    canon = df.loc[_canonical_indices(df)]
    for _, group in canon.groupby("normalized_title"):
        if len(group) > 1 and group["normalized_title"].iloc[0]:
            _mark_group(df, group.index.tolist(), "exact_title_14d", require_window=True)

    # Pass 3: fuzzy title similarity.
    _greedy_pairwise(
        df,
        lambda a, b: fuzz.token_set_ratio(
            df.loc[a, "normalized_title"], df.loc[b, "normalized_title"]
        ) >= DEDUPE_FUZZY_THRESHOLD,
        "fuzzy_title_90",
    )

    # Pass 4 (optional): TF-IDF cosine on title + snippet.
    # Honest caveat: GDELT artlist rows have no snippets, so for them this runs on
    # title-only and adds little beyond fuzzy matching — its value is on
    # snippet-bearing rows (Google News, PR wires) and as a second, independent
    # near-dup method. If it causes false merges on short titles, raise the
    # threshold to 0.85 rather than debugging.
    remaining = _canonical_indices(df)
    if len(remaining) >= 2:
        texts = [
            f"{df.loc[i, 'normalized_title']} {df.loc[i, 'snippet'] or ''}".strip()
            for i in remaining
        ]
        try:
            matrix = TfidfVectorizer(min_df=1, stop_words="english").fit_transform(texts)
            sim = cosine_similarity(matrix)
        except ValueError:  # e.g. all-empty vocabulary
            sim = None
        if sim is not None:
            pos = {idx: p for p, idx in enumerate(remaining)}
            _greedy_pairwise(
                df,
                lambda a, b: sim[pos[a], pos[b]] >= DEDUPE_TFIDF_THRESHOLD,
                "tfidf_similarity_82",
            )

    # Resolve duplicate_of chains: if X -> Y and Y later became a duplicate of Z,
    # re-point X at Z so duplicate_of always references a canonical row.
    id_to_idx = {df.loc[i, "article_id"]: i for i in df.index}
    for idx in df.index[df["dedupe_status"] == "duplicate"]:
        target = df.loc[idx, "duplicate_of"]
        seen = set()
        while target in id_to_idx and df.loc[id_to_idx[target], "dedupe_status"] == "duplicate":
            if target in seen:  # safety against cycles
                break
            seen.add(target)
            target = df.loc[id_to_idx[target], "duplicate_of"]
        df.loc[idx, "duplicate_of"] = target

    return df
