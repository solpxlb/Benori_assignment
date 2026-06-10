"""No-network tests for the DealLens FMCG pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from src.cleaning import apply_cleaning, canonicalize_url, normalize_title
from src.clustering import cluster_deals
from src.dedupe import dedupe
from src.pipeline import run_pipeline
from src.sample import load_sample
from src.scoring import get_tier, score_articles
from src.sources._common import rows_to_df, empty_articles_df


def _row(
    article_id: str,
    title: str,
    snippet: str = "",
    url: str | None = None,
    source_name: str = "Example Source",
    query: str = "general_fmcg",
    published_at: datetime | None = None,
    retrieved_at: datetime | None = None,
) -> dict:
    """Build one schema-aligned raw article row for tests."""
    now = datetime.now(timezone.utc)
    return {
        "article_id": article_id,
        "title": title,
        "snippet": snippet,
        "url": url or f"https://example.com/{article_id}",
        "source_name": source_name,
        "published_at": published_at or now,
        "retrieved_at": retrieved_at or now,
        "source_api": "test",
        "query": query,
        "is_sample": False,
    }


def _processed(rows: list[dict]) -> pd.DataFrame:
    """Clean, dedupe, and score test rows."""
    return score_articles(dedupe(apply_cleaning(rows_to_df(rows))))


def test_canonicalize_url_strips_tracking_and_sorts_params() -> None:
    """URL canonicalization strips tracking, fragments, www, and sorts params."""
    url = "https://www.Site.com/a/?utm_source=x&b=2&a=1#frag"
    assert canonicalize_url(url) == "https://site.com/a?a=1&b=2"


def test_normalize_title_breaking_suffix_variant_matches() -> None:
    """Headline normalization removes noise prefixes and short source suffixes."""
    assert normalize_title("BREAKING: X Acquires Y - Reuters") == normalize_title("X acquires Y")


def test_exact_url_duplicates_are_flagged() -> None:
    """Same canonical URL rows are retained but duplicate-flagged."""
    rows = [
        _row("a1", "Example Foods acquires Sample Snacks Co", url="https://site.com/a?utm_source=x"),
        _row("a2", "Different wording on same article", url="https://site.com/a"),
    ]
    out = dedupe(apply_cleaning(rows_to_df(rows)))
    assert len(out) == 2
    dupes = out[out["dedupe_status"] == "duplicate"]
    assert len(dupes) == 1
    assert dupes.iloc[0]["dedupe_reason"] == "exact_url"


def test_fuzzy_title_duplicates_at_threshold_and_distinct_titles_stay_canonical() -> None:
    """Fuzzy title pass catches high-similarity variants but not unrelated titles."""
    fuzzy_rows = [
        _row("a1", "Example Foods acquires Sample Snacks Co for $120 million"),
        _row("a2", "Example Foods acquires Sample Snacks Co for $120 mln", url="https://example.org/a2"),
    ]
    fuzzy_out = dedupe(apply_cleaning(rows_to_df(fuzzy_rows)))
    assert "fuzzy_title_90" in set(fuzzy_out["dedupe_reason"])

    distinct_rows = [
        _row("b1", "Example Foods acquires Sample Snacks Co for $120 million"),
        _row("b2", "Demo Beauty Brands raises $40M to expand skincare line", url="https://example.org/b2"),
    ]
    distinct_out = dedupe(apply_cleaning(rows_to_df(distinct_rows)))
    assert set(distinct_out["dedupe_status"]) == {"canonical"}


def test_clear_acquisition_article_is_included() -> None:
    """A visible FMCG acquisition with value scores include."""
    rows = [
        _row(
            "a1",
            "Example Foods acquires Sample Snacks Co for $120 million",
            "Consumer goods company Example Foods completed the acquisition of Sample Snacks Co.",
        )
    ]
    out = _processed(rows)
    assert out.iloc[0]["status"] == "include"
    assert out.iloc[0]["relevance_score"] >= 70
    assert "strong_deal_term" in out.iloc[0]["relevance_reasons"]


def test_earnings_article_rejects_with_earnings_noise() -> None:
    """Earnings noise without a strong deal term is rejected with reason code."""
    rows = [
        _row(
            "e1",
            "Example Consumer Corp reports quarterly results",
            "Consumer goods earnings rose, dividend increased, and the stock price moved higher.",
        )
    ]
    out = _processed(rows)
    assert out.iloc[0]["status"] == "reject"
    assert out.iloc[0]["relevance_score"] < 55
    assert "earnings_noise" in out.iloc[0]["relevance_reasons"]


def test_no_deal_term_score_is_capped() -> None:
    """Rows without deal language are capped at 30 and carry no_deal_term."""
    rows = [
        _row(
            "n1",
            "Demo Beauty launches new skincare range",
            "The product launch includes a marketing campaign for a skincare line.",
            query="beauty_personal_care",
        )
    ]
    out = _processed(rows)
    assert out.iloc[0]["relevance_score"] <= 30
    assert out.iloc[0]["status"] == "reject"
    assert "no_deal_term" in out.iloc[0]["relevance_reasons"]


def test_tier_lookup_domain_unknown_and_name_fallback() -> None:
    """Credibility tier lookup handles domain, unknown, and Google News source-name fallback."""
    assert get_tier("reuters.com") == (1, 90)
    assert get_tier("unknownblog.net") == (4, 45)
    assert get_tier("news.google.com", "Reuters") == (1, 90)


def test_clustering_groups_same_deal_and_separates_different_deal() -> None:
    """Clustering groups related deal evidence and keeps different transactions separate."""
    rows = [
        _row(
            "a1",
            "Example Foods acquires Sample Snacks Co for $120 million",
            "Consumer goods company Example Foods completed the acquisition of Sample Snacks Co.",
        ),
        _row(
            "a2",
            "Sample Snacks Co sold to Example Foods as packaged foods consolidation continues",
            "The $120 million takeover hands Example Foods a snacks brand.",
            url="https://example.org/a2",
        ),
        _row(
            "b1",
            "Demo Beauty Brands raises $40M to expand skincare line",
            "Demo Beauty Brands, a consumer brands company focused on skincare, raises $40 million.",
            url="https://beauty.example.com/b1",
            query="pe_funding",
        ),
    ]
    processed = _processed(rows)
    clustered, clusters = cluster_deals(processed)
    assert len(clusters) == 2
    cluster_sizes = sorted(c["article_count"] for c in clusters)
    assert cluster_sizes == [1, 2]
    acquisition_cluster = next(c for c in clusters if c["article_count"] == 2)
    assert acquisition_cluster["deal_type"] == "acquisition"
    funding_cluster = next(c for c in clusters if c["article_count"] == 1)
    assert funding_cluster["deal_type"] == "funding"
    assert clustered["cluster_id"].nunique() == 2


def test_sample_loader_returns_fresh_sample_rows() -> None:
    """Sample loader returns 14 fresh, visibly sample rows."""
    sample = load_sample()
    assert len(sample) == 14
    assert sample["is_sample"].eq(True).all()
    published = pd.to_datetime(sample["published_at"], utc=True)
    age_days = (pd.Timestamp.now(tz="UTC") - published).dt.total_seconds() / 86400
    assert age_days.min() >= 0
    assert age_days.max() < 15


def test_run_pipeline_fetch_failure_falls_back_without_mixing(tmp_path: Path) -> None:
    """A fetch exception returns sample-mode output and never mixes live/sample rows."""
    def failing_fetch(timespan: str) -> pd.DataFrame:
        raise RuntimeError("network down")

    result = run_pipeline(
        "7d", 60, 50, True, fetch_func=failing_fetch, output_dir=tmp_path
    )
    assert result.run_metadata["used_sample"] is True
    assert "network down" in result.run_metadata["fetch_warning"]
    assert result.raw_df["is_sample"].eq(True).all()
    assert result.processed_df["is_sample"].eq(True).all()
    assert result.newsletter_data["data_mode"] == "SAMPLE DATA"
    assert result.export_artifacts["newsletter_docx"].path.exists()


def test_run_pipeline_thresholds_change_cluster_count_without_refetch(tmp_path: Path) -> None:
    """Threshold changes alter displayed clusters while using the supplied fetch result."""
    calls = {"count": 0}

    def sample_fetch(timespan: str) -> pd.DataFrame:
        calls["count"] += 1
        return load_sample()

    low = run_pipeline("7d", 60, 50, False, fetch_func=sample_fetch, output_dir=tmp_path)
    high = run_pipeline("7d", 80, 50, False, fetch_func=sample_fetch, output_dir=tmp_path)
    assert calls["count"] == 2
    assert len(low.clusters) > len(high.clusters)
    assert low.run_metadata["used_sample"] is False
    assert high.run_metadata["used_sample"] is False


def test_run_pipeline_empty_no_fallback_stays_live(tmp_path: Path) -> None:
    """Empty live data without fallback remains LIVE and does not crash."""
    result = run_pipeline(
        "7d", 60, 50, False, fetch_func=lambda _: empty_articles_df(), output_dir=tmp_path
    )
    assert result.run_metadata["used_sample"] is False
    assert result.newsletter_data["data_mode"] == "LIVE"
    assert result.clusters == []
