"""No-network tests for the DealLens FMCG pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import unescape
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
from docx import Document

from src.cleaning import apply_cleaning, canonicalize_url, normalize_title
from src.clustering import cluster_deals, extract_companies
from src.dedupe import dedupe
from src.newsletter import generate_newsletter
from src.pipeline import run_pipeline
from src.sample import load_sample
from src.scoring import get_tier, score_articles
from src.sources.google_news import fetch_google_news
from src.sources._common import rows_to_df, empty_articles_df
from src.llm_newsletter import clear_polish_cache, polish_newsletter, validate_polish


def _row(
    article_id: str,
    title: str,
    snippet: str = "",
    url: str | None = None,
    source_name: str = "Example Source",
    query: str = "general_fmcg",
    published_at: datetime | None = None,
    retrieved_at: datetime | None = None,
    source_api: str = "test",
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
        "source_api": source_api,
        "query": query,
        "is_sample": False,
    }


def _processed(rows: list[dict]) -> pd.DataFrame:
    """Clean, dedupe, and score test rows."""
    return score_articles(dedupe(apply_cleaning(rows_to_df(rows))))


def _valid_ai_cluster_item(cluster: dict) -> dict:
    """Build fact-grounded AI prose without standalone numeric claims."""
    companies = cluster.get("companies")
    companies_text = ", ".join(companies) if isinstance(companies, list) else str(companies or "")
    return {
        "cluster_id": cluster["cluster_id"],
        "headline": f"{companies_text} {cluster['deal_type']} activity",
        "takeaway": f"Signals {cluster['deal_type']} activity in {cluster['category']} with {companies_text}.",
    }


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


def test_stock_holding_bought_language_is_market_noise_not_deal() -> None:
    """Stock-position articles should not pass just because they say bought."""
    rows = [
        _row(
            "m1",
            "360,436 Shares in PepsiCo, Inc. Bought by Capital World Investors",
            "The investor increased its holdings and stock position in PEP shares.",
            source_name="MarketBeat",
            query="watchlist",
        )
    ]
    out = _processed(rows)
    assert out.iloc[0]["status"] == "reject"
    assert "market_noise" in out.iloc[0]["relevance_reasons"]


def test_stock_grant_acquires_shares_is_market_noise_not_deal() -> None:
    """Director stock grants using 'acquires shares' are not FMCG transactions."""
    rows = [
        _row(
            "m2",
            "PepsiCo director David Gibbs acquires shares through deferred fee stock grant",
            "The filing covered a stock grant and position in PepsiCo shares.",
            source_name="Stock Titan",
            query="watchlist",
        )
    ]
    out = _processed(rows)
    assert out.iloc[0]["status"] == "reject"
    assert "market_noise" in out.iloc[0]["relevance_reasons"]


def test_off_topic_crypto_source_does_not_corroborate_fmcg_deal() -> None:
    """Crypto publishers mirroring FMCG headlines should be rejected as off-topic sources."""
    rows = [
        _row(
            "c1",
            "Unilever CEO defends merger with McCormick amid investor concerns",
            source_name="Crypto Briefing",
            query="watchlist",
        )
    ]
    out = _processed(rows)
    assert out.iloc[0]["status"] == "reject"
    assert "off_topic_source" in out.iloc[0]["relevance_reasons"]


def test_tier_lookup_domain_unknown_and_name_fallback() -> None:
    """Credibility tier lookup handles domain, unknown, and Google News source-name fallback."""
    assert get_tier("reuters.com") == (1, 90)
    assert get_tier("", "Inside FMCG") == (2, 75)
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


def test_pipeline_filters_stale_live_rows_before_sample_fallback(tmp_path: Path) -> None:
    """Old live rows cannot satisfy a Last 7 days fallback check."""
    old = datetime.now(timezone.utc) - timedelta(days=90)

    def stale_fetch(timespan: str) -> pd.DataFrame:
        return rows_to_df([
            _row(
                "old1",
                "Example Foods acquires Sample Snacks Co for $120 million",
                "Consumer goods acquisition value $120 million.",
                published_at=old,
                retrieved_at=datetime.now(timezone.utc),
            )
        ])

    result = run_pipeline("7d", 60, 50, True, fetch_func=stale_fetch, output_dir=tmp_path)
    assert result.run_metadata["used_sample"] is True
    assert result.run_metadata["live_raw_rows"] == 0
    assert result.raw_df["is_sample"].eq(True).all()


def test_google_news_queries_include_when_scope(monkeypatch) -> None:
    """Google News RSS queries include a when: token for the selected date range."""
    seen_urls: list[str] = []

    class Response:
        status_code = 200
        content = b"<rss><channel></channel></rss>"

    def fake_get(url: str, timeout: int):
        seen_urls.append(url)
        return Response()

    monkeypatch.setattr("src.sources.google_news.requests.get", fake_get)
    fetch_google_news(["FMCG acquisition"], timespan="7d", max_per_query=1)
    assert seen_urls
    assert "when%3A7d" in seen_urls[0]


def test_buy_language_and_tier1_single_source_becomes_medium_confidence() -> None:
    """Tier-1 recent buy-language rows should not be buried as low confidence."""
    rows = [
        _row(
            "r1",
            "Nestle buys out yfood Labs founders in first acquisition for new CEO - Reuters",
            url="https://news.google.com/rss/articles/r1",
            source_name="Reuters",
            query="watchlist",
            source_api="google_news_rss",
        )
    ]
    processed = _processed(rows)
    row = processed.iloc[0]
    assert row["relevance_score"] >= 60
    assert row["credibility_score"] >= 90
    assert "strong_deal_term" in row["relevance_reasons"]
    _, clusters = cluster_deals(processed)
    assert clusters[0]["confidence"] == "Medium"


def test_cluster_display_uses_article_credibility_not_only_cluster_penalty(tmp_path: Path) -> None:
    """A valid single-source row at the article credibility threshold remains visible."""
    def fetch(timespan: str) -> pd.DataFrame:
        return rows_to_df([
            _row(
                "d1",
                "Disaronno Group completes acquisition of Amaro Averna and Zedda Piras",
                "Food and beverage acquisition announced this week.",
                source_name="Food & Beverage Outlook",
                query="food_beverage",
            )
        ])

    result = run_pipeline("7d", 60, 50, False, fetch_func=fetch, output_dir=tmp_path)
    assert len(result.clusters) == 1
    assert result.clusters[0]["credibility_score"] < 50
    assert result.clusters[0]["max_article_credibility"] >= 50


def test_ai_polish_without_key_falls_back(tmp_path: Path) -> None:
    """AI polish is optional and deterministic output remains available with no key."""
    result = run_pipeline(
        "7d",
        60,
        50,
        True,
        use_ai_polish=True,
        openrouter_api_key="",
        fetch_func=lambda _: empty_articles_df(),
        output_dir=tmp_path,
    )
    assert result.newsletter_data["ai_polished"] is False
    assert result.run_metadata["ai_polish_used"] is False
    assert result.run_metadata["ai_polish_status"] == "disabled"
    assert "FMCG DealBrief" in result.newsletter_md


def test_ai_polish_rejects_unknown_cluster_id() -> None:
    """Validation rejects model output that does not preserve cluster IDs."""
    _, newsletter_data = generate_newsletter([], empty_articles_df(), is_sample=True)
    newsletter_data["highlights"] = [{
        "cluster_id": "C001",
        "canonical_headline": "Example Foods acquires Sample Snacks Co",
        "deal_type": "acquisition",
        "companies": ["Example Foods", "Sample Snacks Co"],
        "category": "Snacks & Packaged Foods",
        "geography": "Not specified",
        "deal_value": "undisclosed",
        "confidence": "Medium",
        "relevance_score": 80,
        "credibility_score": 68,
        "source_count": 2,
        "why_it_matters": "Signals acquisition activity in Snacks & Packaged Foods.",
    }]
    newsletter_data["watchlist"] = []
    ok, reason = validate_polish(
        {
            "executive_summary": "One acquisition cluster is included.",
            "clusters": [{"cluster_id": "C999", "headline": "Example deal", "takeaway": "Uses provided facts."}],
        },
        newsletter_data,
    )
    assert ok is False
    assert "unknown cluster_id" in reason


def test_ai_polish_rejects_new_money_values() -> None:
    """Validation rejects unsupported monetary claims introduced by the model."""
    result = run_pipeline(
        "7d",
        60,
        50,
        True,
        fetch_func=lambda _: empty_articles_df(),
    )
    polished = {
        "executive_summary": "Sample deal activity is concentrated in packaged foods.",
        "clusters": [
            {
                "cluster_id": cluster["cluster_id"],
                "headline": cluster["canonical_headline"],
                "takeaway": "This deal is worth $999 million.",
            }
            for cluster in result.newsletter_data["highlights"] + result.newsletter_data["watchlist"]
        ],
    }
    ok, reason = validate_polish(polished, result.newsletter_data)
    assert ok is False
    assert "unsupported money value" in reason


def test_ai_polish_mock_success(monkeypatch, tmp_path: Path) -> None:
    """A valid mocked OpenRouter response replaces display prose and remains exportable."""
    clear_polish_cache()
    base = run_pipeline(
        "7d",
        60,
        50,
        True,
        fetch_func=lambda _: empty_articles_df(),
        output_dir=tmp_path,
    )
    clusters = base.newsletter_data["highlights"] + base.newsletter_data["watchlist"]
    content = {
        "executive_summary": "Sample FMCG deal activity is led by packaged foods, with beauty and dairy items on watch.",
        "clusters": [_valid_ai_cluster_item(cluster) for cluster in clusters],
    }

    class Response:
        """Minimal requests.Response stand-in."""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": __import__("json").dumps(content)}}]}

    monkeypatch.setattr("src.llm_newsletter.requests.post", lambda *args, **kwargs: Response())
    polished = polish_newsletter(base.newsletter_data, base.newsletter_md, api_key="test-key")
    assert polished.used_ai is True
    assert polished.newsletter_data["ai_polished"] is True
    assert "AI-polished wording" in polished.newsletter_md


def test_ai_polish_mock_success_flows_to_pipeline_exports(monkeypatch, tmp_path: Path) -> None:
    """Validated AI prose is used by markdown, DOCX, and XLSX exports."""
    clear_polish_cache()
    base = run_pipeline(
        "7d",
        60,
        50,
        True,
        fetch_func=lambda _: empty_articles_df(),
        output_dir=tmp_path / "base",
    )
    clusters = base.newsletter_data["highlights"] + base.newsletter_data["watchlist"]
    first_item = _valid_ai_cluster_item(clusters[0])
    polished_headline = first_item["headline"]
    polished_takeaway = first_item["takeaway"]
    content = {
        "executive_summary": "Sample FMCG deal activity is concentrated in packaged foods.",
        "clusters": [
            first_item if index == 0 else _valid_ai_cluster_item(cluster)
            for index, cluster in enumerate(clusters)
        ],
    }

    class Response:
        """Minimal requests.Response stand-in."""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": __import__("json").dumps(content)}}]}

    monkeypatch.setattr("src.llm_newsletter.requests.post", lambda *args, **kwargs: Response())
    result = run_pipeline(
        "7d",
        60,
        50,
        True,
        use_ai_polish=True,
        openrouter_api_key="test-key",
        fetch_func=lambda _: empty_articles_df(),
        output_dir=tmp_path / "polished",
    )
    assert result.run_metadata["ai_polish_used"] is True
    assert "AI-polished wording" in result.newsletter_md
    assert polished_headline in result.newsletter_md
    assert polished_takeaway in result.newsletter_md

    doc = Document(BytesIO(result.export_bytes["newsletter_docx"]))
    doc_text = "\n".join(p.text for p in doc.paragraphs)
    assert "AI-polished wording" in doc_text
    assert content["executive_summary"] in doc_text
    assert polished_headline in doc_text
    assert polished_takeaway in doc_text

    with ZipFile(BytesIO(result.export_bytes["newsletter_xlsx"])) as workbook:
        shared_strings = unescape(workbook.read("xl/sharedStrings.xml").decode("utf-8"))
    assert "AI polished" in shared_strings
    assert "Gemini" not in shared_strings
    assert "google/gemini-3.5-flash" in shared_strings
    assert polished_headline in shared_strings
    assert polished_takeaway in shared_strings


def test_ai_polish_reuses_cache_for_same_input(monkeypatch, tmp_path: Path) -> None:
    """Identical newsletter inputs should not call OpenRouter more than once."""
    clear_polish_cache()
    base = run_pipeline(
        "7d",
        60,
        50,
        True,
        fetch_func=lambda _: empty_articles_df(),
        output_dir=tmp_path,
    )
    clusters = base.newsletter_data["highlights"] + base.newsletter_data["watchlist"]
    content = {
        "executive_summary": "Sample FMCG deal activity is concentrated in packaged foods.",
        "clusters": [_valid_ai_cluster_item(cluster) for cluster in clusters],
    }
    calls = {"count": 0}

    class Response:
        """Minimal requests.Response stand-in."""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": __import__("json").dumps(content)}}]}

    def fake_post(*args, **kwargs) -> Response:
        calls["count"] += 1
        return Response()

    monkeypatch.setattr("src.llm_newsletter.requests.post", fake_post)
    first = polish_newsletter(base.newsletter_data, base.newsletter_md, api_key="cache-test-key")
    second = polish_newsletter(base.newsletter_data, base.newsletter_md, api_key="cache-test-key")
    assert calls["count"] == 1
    assert first.used_ai is True
    assert second.used_ai is True
    assert first.newsletter_md == second.newsletter_md


def test_ai_polish_cache_survives_full_pipeline_rerun_timestamp(monkeypatch, tmp_path: Path) -> None:
    """Pipeline reruns with fresh timestamps should reuse the same validated polish."""
    base = run_pipeline(
        "7d",
        60,
        50,
        True,
        fetch_func=lambda _: empty_articles_df(),
        output_dir=tmp_path / "base",
    )
    clusters = base.newsletter_data["highlights"] + base.newsletter_data["watchlist"]
    content = {
        "executive_summary": "Sample FMCG deal activity is concentrated in packaged foods.",
        "clusters": [_valid_ai_cluster_item(cluster) for cluster in clusters],
    }
    calls = {"count": 0}
    clear_polish_cache()

    class Response:
        """Minimal requests.Response stand-in."""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": __import__("json").dumps(content)}}]}

    def fake_post(*args, **kwargs) -> Response:
        calls["count"] += 1
        return Response()

    monkeypatch.setattr("src.llm_newsletter.requests.post", fake_post)
    first = run_pipeline(
        "7d",
        60,
        50,
        True,
        use_ai_polish=True,
        openrouter_api_key="pipeline-cache-key",
        fetch_func=lambda _: empty_articles_df(),
        output_dir=tmp_path / "first",
    )
    second = run_pipeline(
        "7d",
        60,
        50,
        True,
        use_ai_polish=True,
        openrouter_api_key="pipeline-cache-key",
        fetch_func=lambda _: empty_articles_df(),
        output_dir=tmp_path / "second",
    )
    assert calls["count"] == 1
    assert first.run_metadata["ai_polish_used"] is True
    assert second.run_metadata["ai_polish_used"] is True
    assert first.newsletter_data["run_timestamp"] != ""
    assert second.newsletter_data["run_timestamp"] != ""


def test_ai_polish_malformed_json_falls_back(monkeypatch) -> None:
    """Malformed OpenRouter output never replaces deterministic newsletter content."""
    clear_polish_cache()
    base = run_pipeline(
        "7d",
        60,
        50,
        True,
        fetch_func=lambda _: empty_articles_df(),
    )

    class Response:
        """Minimal malformed response stand-in."""

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": "not json"}}]}

    monkeypatch.setattr("src.llm_newsletter.requests.post", lambda *args, **kwargs: Response())
    polished = polish_newsletter(base.newsletter_data, base.newsletter_md, api_key="test-key")
    assert polished.used_ai is False
    assert polished.status == "fallback"
    assert polished.newsletter_md == base.newsletter_md


def test_ai_polish_rejects_new_entity_and_geography_terms() -> None:
    """Validation rejects company/geography-like terms that were not in cluster facts."""
    result = run_pipeline(
        "7d",
        60,
        50,
        True,
        fetch_func=lambda _: empty_articles_df(),
    )
    polished = {
        "executive_summary": "Sample FMCG deal activity is concentrated in packaged foods.",
        "clusters": [
            {
                "cluster_id": cluster["cluster_id"],
                "headline": cluster["canonical_headline"],
                "takeaway": "Brazil expansion by Omega Foods is highlighted.",
            }
            for cluster in result.newsletter_data["highlights"] + result.newsletter_data["watchlist"]
        ],
    }
    ok, reason = validate_polish(polished, result.newsletter_data)
    assert ok is False
    assert "unsupported term" in reason


def test_ai_polish_rejects_links_dates_and_percentages() -> None:
    """Validation rejects model-created links, dates, and percentages."""
    result = run_pipeline(
        "7d",
        60,
        50,
        True,
        fetch_func=lambda _: empty_articles_df(),
    )
    clusters = result.newsletter_data["highlights"] + result.newsletter_data["watchlist"]

    def output_with(takeaway: str) -> dict:
        return {
            "executive_summary": "Sample FMCG deal activity is concentrated in packaged foods.",
            "clusters": [
                {
                    "cluster_id": cluster["cluster_id"],
                    "headline": cluster["canonical_headline"],
                    "takeaway": takeaway if index == 0 else cluster["why_it_matters"],
                }
                for index, cluster in enumerate(clusters)
            ],
        }

    ok, reason = validate_polish(output_with("Read more at https://example.com."), result.newsletter_data)
    assert ok is False
    assert "link-like text" in reason

    ok, reason = validate_polish(output_with("Expected to close in January 2027."), result.newsletter_data)
    assert ok is False
    assert "unsupported date" in reason

    ok, reason = validate_polish(output_with("Management expects a 25% margin lift."), result.newsletter_data)
    assert ok is False
    assert "unsupported date or percentage" in reason


def test_ai_polish_rejects_unsupported_plain_numbers() -> None:
    """Validation rejects numeric claims not present in the deterministic input."""
    result = run_pipeline(
        "7d",
        60,
        50,
        True,
        fetch_func=lambda _: empty_articles_df(),
    )
    clusters = result.newsletter_data["highlights"] + result.newsletter_data["watchlist"]
    existing_score = int(clusters[0]["relevance_score"])
    polished = {
        "executive_summary": f"Sample FMCG deal activity includes {existing_score} source signals.",
        "clusters": [
            {
                "cluster_id": cluster["cluster_id"],
                "headline": cluster["canonical_headline"],
                "takeaway": cluster["why_it_matters"],
            }
            for cluster in clusters
        ],
    }
    ok, reason = validate_polish(polished, result.newsletter_data)
    assert ok is False
    assert "unsupported number" in reason


def test_company_extraction_filters_headline_action_junk() -> None:
    """Capitalized phrase extraction should avoid visible action/finance fragments."""
    companies = extract_companies(
        "Ares Leads $1.6 Billion Debt Financing to Support Suave Brands and Elida Beauty Merger to Create Evermark - Business Wire"
    )
    assert "Billion Debt Financing" not in companies
    assert "Support Suave Brands" not in companies
    assert "Business Wire" not in companies
    assert "Elida Beauty" in companies


def test_newsletter_markdown_escapes_dollar_values() -> None:
    """Streamlit Markdown should not treat deal values as math spans."""
    cluster = {
        "cluster_id": "C001",
        "canonical_headline": "Ares leads $1.6 Billion financing",
        "deal_type": "merger",
        "companies": ["Elida Beauty"],
        "geography": "US",
        "deal_value": "$1.6 Billion",
        "confidence": "Medium",
        "relevance_score": 70,
        "credibility_score": 80,
        "source_count": 1,
        "article_count": 1,
        "category": "Beauty & Personal Care",
        "why_it_matters": "Signals merger activity, value $1.6 Billion.",
        "sources": [],
        "latest_published_at": datetime.now(timezone.utc).isoformat(),
        "is_sample": False,
    }
    markdown, _ = generate_newsletter([cluster], pd.DataFrame(), "Last 7 days", False)
    assert r"\$1.6 Billion" in markdown
    assert "$1.6 Billion" not in markdown.replace(r"\$1.6 Billion", "")
