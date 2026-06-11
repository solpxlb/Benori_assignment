"""Pipeline orchestration for DealLens FMCG.

This module owns the exact phase order:
fetch -> clean -> dedupe -> score -> optional sample replacement -> thresholds
for clustering/display -> cluster -> newsletter -> exports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd
import logging

from src.cleaning import apply_cleaning
from src.clustering import cluster_deals
from src.dedupe import dedupe
from src.exports import ExportArtifact, export_all
from src.llm_newsletter import polish_newsletter
from src.newsletter import generate_newsletter
from src.sample import load_sample
from src.scoring import score_articles
from src.sources import fetch_all
from src.sources._common import empty_articles_df, filter_by_timespan


FetchFunc = Callable[[str], pd.DataFrame]
logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Complete result bundle for Streamlit and exports."""

    raw_df: pd.DataFrame
    processed_df: pd.DataFrame
    clusters: list[dict]
    newsletter_md: str
    newsletter_data: dict
    export_artifacts: dict[str, ExportArtifact]
    export_bytes: dict[str, bytes]
    run_metadata: dict


def _process_articles(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Clean, dedupe, and score a raw article frame."""
    if raw_df.empty:
        return score_articles(dedupe(apply_cleaning(raw_df)))
    return score_articles(dedupe(apply_cleaning(raw_df)))


def _included_canonical_count(processed_df: pd.DataFrame) -> int:
    """Count canonical rows with status include after scoring."""
    if processed_df.empty:
        return 0
    return int(
        (
            (processed_df["dedupe_status"] == "canonical")
            & (processed_df["status"] == "include")
        ).sum()
    )


def _threshold_candidates(processed_df: pd.DataFrame, min_relevance: int,
                          min_credibility: int) -> pd.DataFrame:
    """Rows allowed to enter clustering after sidebar display thresholds."""
    if processed_df.empty:
        return processed_df.copy()
    mask = (
        (processed_df["dedupe_status"] == "canonical")
        & (processed_df["status"].isin(["include", "watchlist"]))
        & (processed_df["relevance_score"] >= min_relevance)
        & (processed_df["credibility_score"] >= min_credibility)
    )
    return processed_df.loc[mask].copy()


def _merge_cluster_ids(processed_df: pd.DataFrame, clustered_subset: pd.DataFrame) -> pd.DataFrame:
    """Attach cluster IDs from clustered candidates back onto the full processed frame."""
    merged = processed_df.copy()
    merged["cluster_id"] = ""
    if not clustered_subset.empty and "cluster_id" in clustered_subset:
        merged.loc[clustered_subset.index, "cluster_id"] = clustered_subset["cluster_id"]
    return merged


def _filter_clusters(clusters: list[dict], min_relevance: int, min_credibility: int) -> list[dict]:
    """Apply sidebar thresholds to displayed clusters after aggregation."""
    return [
        cluster for cluster in clusters
        if int(cluster.get("relevance_score") or 0) >= min_relevance
        and (
            int(cluster.get("credibility_score") or 0) >= min_credibility
            or int(cluster.get("max_article_credibility") or 0) >= min_credibility
        )
    ]


def _drop_hidden_cluster_ids(processed_df: pd.DataFrame, clusters: list[dict]) -> pd.DataFrame:
    """Clear cluster IDs for rows whose cluster is filtered from display."""
    visible_ids = {cluster["cluster_id"] for cluster in clusters}
    if not visible_ids or processed_df.empty:
        out = processed_df.copy()
        out["cluster_id"] = ""
        return out
    out = processed_df.copy()
    out.loc[~out["cluster_id"].isin(visible_ids), "cluster_id"] = ""
    return out


def _timespan_label(timespan: str) -> str:
    """Human-readable label for newsletter headers."""
    return {"1d": "Last 24 hours", "7d": "Last 7 days", "30d": "Last 30 days"}.get(
        timespan, timespan
    )


def _source_counts(df: pd.DataFrame) -> dict[str, int]:
    """Return row counts by source API for run diagnostics."""
    if df.empty or "source_api" not in df:
        return {}
    return {str(k): int(v) for k, v in df["source_api"].value_counts().items()}


def _source_warning(raw_df: pd.DataFrame) -> str:
    """Human-readable warning when source contribution is materially degraded."""
    counts = _source_counts(raw_df)
    if not counts:
        return "No live source rows matched the selected date range."
    if counts.get("gdelt", 0) == 0:
        return "Primary source GDELT returned no rows for this run; results rely on RSS sources."
    return ""


def run_pipeline(
    timespan: str = "7d",
    min_relevance: int = 60,
    min_credibility: int = 50,
    use_sample_fallback: bool = True,
    use_ai_polish: bool = False,
    openrouter_api_key: str | None = None,
    fetch_func: FetchFunc = fetch_all,
    output_dir: Path | str = "outputs",
) -> PipelineResult:
    """Run DealLens FMCG end to end and return data, newsletter, and exports.

    Fallback is deliberately post-scoring: live rows are fetched, cleaned, deduped,
    and scored before deciding whether fewer than five canonical include rows means
    the run should be replaced by clearly labeled sample data. Sample and live rows
    are never mixed.
    """
    fetch_warning = ""
    try:
        live_raw_df = fetch_func(timespan)
    except Exception as exc:
        logger.warning("Fetch function failed: %s", exc)
        fetch_warning = f"Fetch failed: {exc}"
        live_raw_df = empty_articles_df()
    if live_raw_df is None:
        fetch_warning = "Fetch returned no data frame."
        live_raw_df = empty_articles_df()
    live_raw_df = filter_by_timespan(live_raw_df, timespan)
    live_processed_df = _process_articles(live_raw_df)
    live_include_count = _included_canonical_count(live_processed_df)

    used_sample = False
    raw_df = live_raw_df
    processed_df = live_processed_df
    fallback_reason = ""

    if live_include_count < 5 and use_sample_fallback:
        used_sample = True
        fallback_reason = (
            f"Live run produced {live_include_count} canonical include row(s), below fallback threshold 5."
        )
        raw_df = load_sample()
        processed_df = _process_articles(raw_df)

    candidates = _threshold_candidates(processed_df, min_relevance, min_credibility)
    clustered_candidates, all_clusters = cluster_deals(candidates)
    processed_with_clusters = _merge_cluster_ids(processed_df, clustered_candidates)
    clusters = _filter_clusters(all_clusters, min_relevance, min_credibility)
    processed_with_clusters = _drop_hidden_cluster_ids(processed_with_clusters, clusters)

    newsletter_md, newsletter_data = generate_newsletter(
        clusters,
        processed_with_clusters,
        date_range_label=_timespan_label(timespan),
        is_sample=used_sample,
    )
    ai_status = "disabled"
    ai_warning = ""
    ai_used = False
    if use_ai_polish:
        polish = polish_newsletter(
            newsletter_data,
            newsletter_md,
            api_key=openrouter_api_key,
        )
        newsletter_md = polish.newsletter_md
        newsletter_data = polish.newsletter_data
        ai_status = polish.status
        ai_warning = polish.warning
        ai_used = polish.used_ai
    else:
        newsletter_data["ai_polished"] = False

    export_artifacts = export_all(
        raw_df,
        processed_with_clusters,
        clusters,
        newsletter_md,
        newsletter_data,
        output_dir=output_dir,
    )
    export_bytes = {name: artifact.data for name, artifact in export_artifacts.items()}

    run_metadata = {
        "timespan": timespan,
        "date_range_label": _timespan_label(timespan),
        "min_relevance": min_relevance,
        "min_credibility": min_credibility,
        "use_sample_fallback": use_sample_fallback,
        "used_sample": used_sample,
        "fallback_reason": fallback_reason,
        "fetch_warning": fetch_warning,
        "source_warning": _source_warning(live_raw_df),
        "source_counts": _source_counts(live_raw_df),
        "live_raw_rows": len(live_raw_df),
        "raw_rows": len(raw_df),
        "processed_rows": len(processed_with_clusters),
        "live_include_count": live_include_count,
        "include_count": _included_canonical_count(processed_df),
        "duplicate_count": int((processed_with_clusters["dedupe_status"] == "duplicate").sum())
        if not processed_with_clusters.empty else 0,
        "cluster_count": len(clusters),
        "high_confidence_count": sum(c.get("confidence") == "High" for c in clusters),
        "highlight_count": sum(c.get("confidence") in {"High", "Medium"} for c in clusters),
        "use_ai_polish": use_ai_polish,
        "ai_polish_used": ai_used,
        "ai_polish_status": ai_status,
        "ai_polish_warning": ai_warning,
    }

    return PipelineResult(
        raw_df=raw_df,
        processed_df=processed_with_clusters,
        clusters=clusters,
        newsletter_md=newsletter_md,
        newsletter_data=newsletter_data,
        export_artifacts=export_artifacts,
        export_bytes=export_bytes,
        run_metadata=run_metadata,
    )
