"""Streamlit demo app for DealLens FMCG."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.pipeline import PipelineResult, run_pipeline
from src.sources import fetch_all
from src.sources._common import filter_by_timespan


TIMESPAN_OPTIONS = {
    "Last 24 hours": "1d",
    "Last 7 days": "7d",
    "Last 30 days": "30d",
}
ARCHITECTURE_PATH = Path(__file__).resolve().parent / "docs" / "architecture.mmd"
CACHE_VERSION = 2  # bump to invalidate Streamlit Cloud cached fetch results after pipeline hotfixes


st.set_page_config(page_title="DealLens FMCG", page_icon="DL", layout="wide")


@st.cache_data(ttl=1800, show_spinner=False)
def cached_fetch_all(timespan: str, cache_version: int = CACHE_VERSION) -> pd.DataFrame:
    """Cached fetch layer; sliders re-run downstream logic without refetching."""
    del cache_version
    return filter_by_timespan(fetch_all(timespan), timespan)


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


def _render_sidebar() -> tuple[str, int, int, bool, bool]:
    """Render sidebar controls and return selected values."""
    st.sidebar.title("DealLens FMCG")
    date_label = st.sidebar.selectbox("Date range", list(TIMESPAN_OPTIONS), index=1)
    min_relevance = st.sidebar.slider("Minimum relevance", 0, 100, 60, 5)
    min_credibility = st.sidebar.slider("Minimum credibility", 0, 100, 50, 5)
    use_sample_fallback = st.sidebar.checkbox("Use sample fallback", value=True)
    run_clicked = st.sidebar.button("Run Pipeline", type="primary")
    st.sidebar.caption("Fetch results are cached for 30 minutes; threshold changes re-score and re-cluster without refetching.")
    return TIMESPAN_OPTIONS[date_label], min_relevance, min_credibility, use_sample_fallback, run_clicked


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
    timespan, min_relevance, min_credibility, use_sample_fallback, run_clicked = _render_sidebar()
    if run_clicked:
        cached_fetch_all.clear()
        st.session_state["has_run"] = True

    st.title("DealLens FMCG")
    st.caption("FMCG DealBrief converts public news into evidence-backed deal-event clusters.")

    if not st.session_state.get("has_run"):
        st.info("Choose settings in the sidebar and run the pipeline.")
        return

    with st.spinner("Running ingestion, scoring, clustering, newsletter, and exports..."):
        result = run_pipeline(
            timespan=timespan,
            min_relevance=min_relevance,
            min_credibility=min_credibility,
            use_sample_fallback=use_sample_fallback,
            fetch_func=lambda ts: cached_fetch_all(ts, CACHE_VERSION),
            output_dir="outputs",
        )

    st.sidebar.success(_metadata_text(result))
    if result.run_metadata.get("fetch_warning"):
        st.sidebar.warning(result.run_metadata["fetch_warning"])
    if result.run_metadata.get("source_warning") and not result.run_metadata["used_sample"]:
        st.sidebar.warning(result.run_metadata["source_warning"])
    if result.run_metadata["used_sample"]:
        st.warning(
            "SAMPLE DATA mode is active. Live rows were discarded and replaced entirely with synthetic sample rows."
        )
        if result.run_metadata["fallback_reason"]:
            st.caption(result.run_metadata["fallback_reason"])

    tab1, tab2, tab3, tab4 = st.tabs([
        "📰 Snapshot & Newsletter",
        "🔍 Deal Clusters",
        "🧹 Data & Transparency",
        "📖 Methodology",
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
