"""Streamlit demo app for DealLens FMCG."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.pipeline import PipelineResult, run_pipeline
from src.sources import fetch_all
from src.sources._common import empty_articles_df, filter_by_timespan


TIMESPAN_OPTIONS = {
    "Last 24 hours": "1d",
    "Last 7 days": "7d",
    "Last 30 days": "30d",
}
ARCHITECTURE_PATH = Path(__file__).resolve().parent / "docs" / "architecture.mmd"
CACHE_VERSION = 2  # bump to invalidate Streamlit Cloud cached fetch results after pipeline hotfixes
DEFAULT_TIMESPAN_LABEL = "Last 7 days"
RUN_MODE_SAMPLE = "sample"
RUN_MODE_LIVE = "live"

APP_CSS = """
<style>
    :root {
        --dl-bg: #f6f7f9;
        --dl-ink: #17202a;
        --dl-muted: #667085;
        --dl-line: #d7dce3;
        --dl-panel: #ffffff;
        --dl-accent: #2457c5;
        --dl-accent-soft: #e8eefc;
    }

    .stApp {
        background: var(--dl-bg);
        color: var(--dl-ink);
    }

    [data-testid="stSidebar"] {
        background: #ffffff;
        border-right: 1px solid var(--dl-line);
    }

    [data-testid="stSidebar"] h1,
    [data-testid="stSidebar"] h2,
    [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] label {
        color: var(--dl-ink);
    }

    .block-container {
        max-width: 1180px;
        padding-top: 2.25rem;
        padding-bottom: 3rem;
    }

    .dl-hero {
        border: 1px solid var(--dl-line);
        border-radius: 16px;
        background: linear-gradient(135deg, #ffffff 0%, #f7f9ff 100%);
        padding: 28px;
        margin-bottom: 22px;
    }

    .dl-hero h1 {
        font-size: clamp(2.1rem, 4vw, 4rem);
        line-height: 1;
        letter-spacing: -0.04em;
        margin: 0 0 12px 0;
        color: var(--dl-ink);
    }

    .dl-hero p {
        max-width: 760px;
        color: var(--dl-muted);
        font-size: 1rem;
        line-height: 1.55;
        margin: 0;
    }

    .dl-pill-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 18px;
    }

    .dl-pill {
        border: 1px solid var(--dl-line);
        border-radius: 999px;
        background: #ffffff;
        color: #344054;
        font-size: 0.84rem;
        padding: 7px 11px;
        white-space: nowrap;
    }

    .dl-card-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 14px;
        margin-bottom: 18px;
    }

    .dl-card {
        border: 1px solid var(--dl-line);
        border-radius: 14px;
        background: var(--dl-panel);
        padding: 18px;
        min-height: 132px;
    }

    .dl-card strong {
        display: block;
        color: var(--dl-ink);
        font-size: 0.98rem;
        margin-bottom: 8px;
    }

    .dl-card span {
        color: var(--dl-muted);
        font-size: 0.92rem;
        line-height: 1.45;
    }

    .dl-mode-note {
        border: 1px solid var(--dl-line);
        border-left: 4px solid var(--dl-accent);
        border-radius: 12px;
        background: #ffffff;
        padding: 14px 16px;
        color: #344054;
        margin: 12px 0 18px;
    }

    .dl-sample-banner {
        border: 1px solid #d9c88f;
        border-radius: 12px;
        background: #fff7d6;
        color: #4a3b08;
        padding: 13px 16px;
        margin: 14px 0 10px;
        font-weight: 600;
    }

    div[data-testid="stSidebar"] [data-testid="stButton"] button {
        border-radius: 10px;
        min-height: 42px;
        border: 1px solid var(--dl-accent) !important;
        background: var(--dl-accent) !important;
        color: #ffffff !important;
        font-weight: 700;
    }

    div[data-testid="stSidebar"] [data-testid="stButton"] button p,
    div[data-testid="stSidebar"] [data-testid="stButton"] button span,
    div[data-testid="stSidebar"] [data-testid="stButton"] button div {
        color: #ffffff !important;
    }

    button[data-testid="stBaseButton-primary"] *,
    button[data-testid="stBaseButton-primary"] [data-testid="stMarkdownContainer"] p {
        color: #ffffff !important;
    }

    div[data-testid="stSidebar"] [data-testid="stButton"] button:hover {
        border-color: #173f94 !important;
        background: #173f94 !important;
        color: #ffffff !important;
    }

    div[data-testid="stSidebar"] [data-testid="stButton"] button:active {
        transform: translateY(1px);
    }

    div[data-testid="stMetric"] {
        border: 1px solid var(--dl-line);
        border-radius: 14px;
        background: #ffffff;
        padding: 12px 14px;
    }

    div[data-testid="stMetricValue"] {
        color: var(--dl-ink);
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }

    .stTabs [data-baseweb="tab"] {
        border: 1px solid var(--dl-line);
        border-radius: 999px;
        background: #ffffff;
        padding: 8px 14px;
    }

    .stTabs [aria-selected="true"] {
        background: var(--dl-accent-soft);
        border-color: #b7c7f3;
        color: var(--dl-accent);
    }

    @media (max-width: 900px) {
        .dl-card-grid {
            grid-template-columns: 1fr;
        }

        .block-container {
            padding-top: 1.25rem;
        }
    }
</style>
"""


st.set_page_config(page_title="DealLens FMCG", page_icon="DL", layout="wide")


@st.cache_data(ttl=1800, show_spinner=False)
def cached_fetch_all(timespan: str, cache_version: int = CACHE_VERSION) -> pd.DataFrame:
    """Cached fetch layer; sliders re-run downstream logic without refetching."""
    del cache_version
    return filter_by_timespan(fetch_all(timespan), timespan)


def _sample_result(timespan: str, min_relevance: int, min_credibility: int) -> PipelineResult:
    """Fast sample run used for the initial product demo."""
    result = run_pipeline(
        timespan=timespan,
        min_relevance=min_relevance,
        min_credibility=min_credibility,
        use_sample_fallback=True,
        fetch_func=lambda _: empty_articles_df(),
        output_dir="outputs",
    )
    result.run_metadata["fallback_reason"] = ""
    return result


def _display_df(df: pd.DataFrame) -> pd.DataFrame:
    """Make nested/list values readable in Streamlit tables."""
    out = df.copy()
    for col in out.columns:
        if out[col].map(lambda v: isinstance(v, (list, dict))).any():
            out[col] = out[col].map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    return out


def _artifact(result: PipelineResult, key: str):
    """Return an export artifact by key."""
    return result.export_artifacts[key]


def _download_button(label: str, result: PipelineResult, key: str, button_key: str) -> None:
    """Render one download button for a Phase 7 export artifact."""
    artifact = _artifact(result, key)
    st.download_button(
        label,
        data=artifact.data,
        file_name=artifact.filename,
        mime=artifact.mime,
        key=button_key,
    )


def _evidence_table(cluster: dict) -> pd.DataFrame:
    """Build an evidence table for one cluster."""
    rows = []
    for source in cluster.get("sources", []):
        rows.append({
            "Source": source.get("source_name") or source.get("domain") or "source",
            "Title": source.get("title", ""),
            "Published": source.get("published_at", ""),
            "Tier": source.get("credibility_tier", ""),
            "URL": source.get("url", ""),
        })
    return pd.DataFrame(rows)


def _metadata_text(result: PipelineResult) -> str:
    """Compact run metadata for the sidebar."""
    meta = result.run_metadata
    return (
        f"Rows: {meta['processed_rows']} | "
        f"Clusters: {meta['cluster_count']} | "
        f"Duplicates: {meta['duplicate_count']}"
    )


def _render_sidebar() -> tuple[str, int, int, bool, str | None]:
    """Render sidebar controls and return selected values."""
    st.sidebar.title("DealLens FMCG")
    st.sidebar.caption("Generate a skim-ready FMCG deal newsletter from public news signals.")

    date_label = st.sidebar.selectbox(
        "News window",
        list(TIMESPAN_OPTIONS),
        index=list(TIMESPAN_OPTIONS).index(DEFAULT_TIMESPAN_LABEL),
    )

    with st.sidebar.expander("Advanced filters", expanded=False):
        min_relevance = st.slider("Minimum relevance", 0, 100, 60, 5)
        min_credibility = st.slider("Minimum credibility", 0, 100, 50, 5)

    st.sidebar.markdown("**Choose a run type**")
    sample_clicked = st.sidebar.button("Preview with sample data", type="primary", use_container_width=True)
    live_clicked = st.sidebar.button("Scan live public news", use_container_width=True)
    use_sample_fallback = st.sidebar.checkbox("Fallback to sample if live scan is sparse", value=True)
    st.sidebar.caption("Sample mode is instant. Live scans can take about a minute because public sources are rate-limited.")

    action = RUN_MODE_SAMPLE if sample_clicked else RUN_MODE_LIVE if live_clicked else None
    return TIMESPAN_OPTIONS[date_label], min_relevance, min_credibility, use_sample_fallback, action


def _render_hero(run_mode: str) -> None:
    """Render the simplified first impression."""
    mode = "Live public-news scan" if run_mode == RUN_MODE_LIVE else "Sample preview"
    st.markdown(
        f"""
<section class="dl-hero">
  <h1>FMCG DealBrief</h1>
  <p>DealLens turns noisy M&A coverage into deal-event clusters with source evidence, duplicate handling, relevance scores, and credibility signals.</p>
  <div class="dl-pill-row">
    <span class="dl-pill">{mode}</span>
    <span class="dl-pill">One cluster = one transaction</span>
    <span class="dl-pill">DOCX, XLSX, CSV, JSON exports</span>
  </div>
</section>
""",
        unsafe_allow_html=True,
    )


def _render_value_cards() -> None:
    """Render compact explanation cards for first-time evaluators."""
    st.markdown(
        """
<div class="dl-card-grid">
  <div class="dl-card">
    <strong>Ingest</strong>
    <span>Pulls from GDELT, Google News RSS, and confirmed PR-wire feeds. Failures are non-fatal.</span>
  </div>
  <div class="dl-card">
    <strong>Clean and score</strong>
    <span>Normalizes URLs and titles, flags duplicates, then explains each relevance and credibility score.</span>
  </div>
  <div class="dl-card">
    <strong>Brief the user</strong>
    <span>Outputs a short newsletter with deal highlights, watchlist items, evidence links, and export files.</span>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def _render_mode_note(result: PipelineResult) -> None:
    """Show one concise note about the current data mode."""
    if result.run_metadata["used_sample"]:
        text = "You are viewing synthetic sample data so the demo is fast and reproducible. Use Scan live public news when you want current public-source results."
    else:
        text = "You are viewing live public-source results. If sources are sparse or slow, the app can fall back to a clearly labeled sample run."
    st.markdown(f'<div class="dl-mode-note">{text}</div>', unsafe_allow_html=True)


def _render_sample_banner(result: PipelineResult) -> None:
    """Render a high-contrast sample-data notice."""
    if not result.run_metadata["used_sample"]:
        return
    st.markdown(
        '<div class="dl-sample-banner">SAMPLE DATA mode is active. Sample rows are synthetic and clearly labeled in exports.</div>',
        unsafe_allow_html=True,
    )
    if result.run_metadata.get("fallback_reason"):
        st.caption(result.run_metadata["fallback_reason"])


def _render_snapshot_tab(result: PipelineResult) -> None:
    """Render Snapshot & Newsletter tab."""
    meta = result.run_metadata
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Deal Events", meta["cluster_count"])
    c2.metric("Articles", meta["processed_rows"])
    c3.metric("Duplicates Flagged", meta["duplicate_count"])
    c4.metric("Med/High Confidence", meta.get("highlight_count", meta["high_confidence_count"]))

    st.markdown(result.newsletter_md)

    st.subheader("Downloads")
    d1, d2, d3, d4, d5 = st.columns(5)
    with d1:
        _download_button("DOCX", result, "newsletter_docx", "download_docx")
    with d2:
        _download_button("XLSX", result, "newsletter_xlsx", "download_xlsx")
    with d3:
        _download_button("Clusters JSON", result, "clusters_json", "download_clusters_json")
    with d4:
        _download_button("Raw CSV", result, "raw_csv", "download_raw_csv")
    with d5:
        _download_button("Raw JSON", result, "raw_json", "download_raw_json")


def _render_clusters_tab(result: PipelineResult) -> None:
    """Render Deal Clusters tab."""
    if not result.clusters:
        st.info("No clusters met the current thresholds.")
        return
    for cluster in result.clusters:
        label = (
            f"{cluster['cluster_id']} | {cluster['confidence']} | "
            f"{cluster['deal_type']} | {cluster['canonical_headline']}"
        )
        with st.expander(label, expanded=False):
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Relevance", cluster["relevance_score"])
            m2.metric("Credibility", cluster["credibility_score"])
            m3.metric("Sources", cluster["source_count"])
            m4.metric("Articles", cluster["article_count"])
            st.write(
                {
                    "category": cluster["category"],
                    "geography": cluster["geography"],
                    "deal_value": cluster["deal_value"],
                    "companies": cluster["companies"],
                }
            )
            st.markdown(cluster["why_it_matters"])
            evidence = _evidence_table(cluster)
            st.dataframe(
                evidence,
                hide_index=True,
                use_container_width=True,
                column_config={"URL": st.column_config.LinkColumn("URL")},
            )


def _render_transparency_tab(result: PipelineResult) -> None:
    """Render duplicates, rejects, and searchable processed data."""
    df = result.processed_df
    if df.empty:
        st.info("No processed rows.")
        return

    st.subheader("Duplicates")
    duplicates = df[df["dedupe_status"] == "duplicate"]
    st.dataframe(
        _display_df(duplicates[["title", "duplicate_of", "dedupe_reason", "source_name", "url"]]),
        hide_index=True,
        use_container_width=True,
        column_config={"url": st.column_config.LinkColumn("URL")},
    )

    st.subheader("Rejected")
    rejected = df[df["status"] == "reject"]
    rejected_cols = ["title", "relevance_score", "relevance_reasons", "credibility_score", "source_name", "url"]
    st.dataframe(
        _display_df(rejected[rejected_cols]),
        hide_index=True,
        use_container_width=True,
        column_config={"url": st.column_config.LinkColumn("URL")},
    )

    st.subheader("Processed Data")
    query = st.text_input("Search processed rows", placeholder="company, source, reason...")
    full = _display_df(df)
    if query:
        haystack = full.astype(str).agg(" ".join, axis=1)
        full = full[haystack.str.contains(query, case=False, na=False, regex=False)]
    st.dataframe(full, hide_index=True, use_container_width=True)


def _render_methodology_tab() -> None:
    """Render methodology and architecture notes."""
    st.subheader("Pipeline Diagram Source")
    architecture = ARCHITECTURE_PATH.read_text(encoding="utf-8") if ARCHITECTURE_PATH.exists() else ""
    st.code(architecture, language="mermaid")

    st.subheader("Deduplication")
    st.markdown(
        """
- Duplicate rows are retained for transparency but excluded from scoring aggregates and clustering.
- Pass 1: exact canonical URL.
- Pass 2: exact normalized title within 14 days.
- Pass 3: fuzzy title match with token set ratio >= 90 within 14 days.
- Pass 4: TF-IDF similarity >= 0.82 within 14 days, mainly useful on snippet-bearing rows.
"""
    )

    st.subheader("Relevance Scoring")
    st.markdown(
        """
| Signal | Points |
|---|---:|
| Strong deal term | +30 |
| Soft deal term | +20 |
| Direct FMCG/CPG term | +25 |
| Category term | +15 |
| Watchlist company | +15 |
| Recent <= 7d / <= 30d | +10 / +5 |
| Visible deal value | +10 |
| Targeted query context | +5 |
| Earnings noise | -25 |
| Launch noise | -10 |
| No deal term | capped at 30 |
"""
    )

    st.subheader("Credibility")
    st.markdown(
        """
- Tier 1 sources start at 90, tier 2 at 75, tier 3 PR-wire sources at 60, and unknown sources at 45.
- Complete source/date metadata adds +5; missing published date subtracts 10.
- Cluster credibility uses 0.60 * max article credibility + 0.25 * average top-3 credibility + corroboration bonuses.
- A single-source cluster intentionally scores below that source's article credibility because corroboration is part of confidence.
"""
    )

    st.subheader("Limitations")
    st.markdown(
        """
- Free public sources have uneven recall; missed deals are possible.
- Matching and clustering are deterministic heuristics, so false positives and false merges can happen.
- Greedy clustering is order-dependent and compares each article to a cluster seed.
- The app uses headlines, snippets, and metadata only; it does not scrape full articles or bypass paywalls.
"""
    )


def main() -> None:
    """Render the Streamlit app."""
    st.markdown(APP_CSS, unsafe_allow_html=True)
    timespan, min_relevance, min_credibility, use_sample_fallback, action = _render_sidebar()

    if "run_mode" not in st.session_state:
        st.session_state["run_mode"] = RUN_MODE_SAMPLE

    if action == RUN_MODE_SAMPLE:
        st.session_state["run_mode"] = RUN_MODE_SAMPLE
    elif action == RUN_MODE_LIVE:
        cached_fetch_all.clear()
        st.session_state["run_mode"] = RUN_MODE_LIVE

    _render_hero(st.session_state["run_mode"])
    _render_value_cards()

    if st.session_state["run_mode"] == RUN_MODE_LIVE:
        with st.spinner("Scanning public sources. This can take about a minute because free news sources are rate-limited."):
            result = run_pipeline(
                timespan=timespan,
                min_relevance=min_relevance,
                min_credibility=min_credibility,
                use_sample_fallback=use_sample_fallback,
                fetch_func=lambda ts: cached_fetch_all(ts, CACHE_VERSION),
                output_dir="outputs",
            )
    else:
        result = _sample_result(timespan, min_relevance, min_credibility)

    st.sidebar.success(_metadata_text(result))
    if result.run_metadata.get("fetch_warning"):
        st.sidebar.warning(result.run_metadata["fetch_warning"])
    if result.run_metadata.get("source_warning") and not result.run_metadata["used_sample"]:
        st.sidebar.warning(result.run_metadata["source_warning"])
    _render_sample_banner(result)
    _render_mode_note(result)

    tab1, tab2, tab3, tab4 = st.tabs([
        "Newsletter",
        "Deal clusters",
        "Transparency",
        "Methodology",
    ])
    with tab1:
        _render_snapshot_tab(result)
    with tab2:
        _render_clusters_tab(result)
    with tab3:
        _render_transparency_tab(result)
    with tab4:
        _render_methodology_tab()


if __name__ == "__main__":
    main()
