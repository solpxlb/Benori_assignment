"""Newsletter assembly for FMCG DealBrief.

The newsletter is deterministic and evidence-bounded: it presents deal-event
clusters and source links, not generated article summaries.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import pandas as pd


CONFIDENCE_ORDER = {"High": 0, "Medium": 1, "Low": 2}
NEWSLETTER_TITLE = "FMCG DealBrief"


def _parse_time(value) -> pd.Timestamp:
    """Parse a timestamp-like value for sorting; missing values sort old."""
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    return ts if pd.notna(ts) else pd.Timestamp.min.tz_localize("UTC")


def _cluster_sort_key(cluster: dict) -> tuple:
    """Sort key: confidence, relevance, credibility, recency."""
    return (
        CONFIDENCE_ORDER.get(cluster.get("confidence", "Low"), 9),
        -int(cluster.get("relevance_score") or 0),
        -int(cluster.get("credibility_score") or 0),
        -_parse_time(cluster.get("latest_published_at")).timestamp(),
    )


def _source_label(source: dict) -> str:
    """Readable source label for markdown/documents."""
    return source.get("source_name") or source.get("domain") or "source"


def _companies_text(value) -> str:
    """Render cluster companies consistently."""
    if isinstance(value, list):
        return ", ".join(value) if value else "Not clearly identified"
    return value or "Not clearly identified"


def _source_count(clusters: list[dict]) -> int:
    """Count distinct source identities across clusters."""
    keys: set[str] = set()
    for cluster in clusters:
        for source in cluster.get("sources", []):
            key = (source.get("source_name") or source.get("domain") or "").strip().lower()
            if key:
                keys.add(key)
    return len(keys)


def _article_count(clusters: list[dict]) -> int:
    """Count article evidence rows represented by clusters."""
    return sum(int(c.get("article_count") or 0) for c in clusters)


def _methodology_sentences(data_mode: str) -> list[str]:
    """Four-sentence methodology note for the newsletter."""
    mode_note = "This sample-data run" if data_mode == "SAMPLE DATA" else "This live-data run"
    return [
        f"{mode_note} uses public headlines, RSS snippets, and metadata as its only source material.",
        "Articles are cleaned, de-duplicated, and grouped into deal-event clusters before newsletter assembly.",
        "Relevance and credibility scores come from fixed, inspectable rules covering deal terms, FMCG signals, recency, visible values, source tiers, and corroboration.",
        "Deal values, companies, geographies, and source evidence are only shown when present in the input text or metadata; otherwise the newsletter says undisclosed or not specified.",
    ]


def generate_newsletter(
    clusters: list[dict],
    articles: pd.DataFrame,
    date_range_label: str = "Selected range",
    is_sample: bool | None = None,
    run_timestamp: datetime | None = None,
) -> tuple[str, dict]:
    """Build the FMCG DealBrief markdown string and structured newsletter dict.

    `clusters` should be the list returned by `cluster_deals`; `articles` is the
    processed article DataFrame. The returned dict is used by DOCX/XLSX exports
    so the Streamlit layer does not need to parse markdown.
    """
    run_timestamp = run_timestamp or datetime.now(timezone.utc)
    if is_sample is None:
        is_sample = bool(clusters and all(c.get("is_sample") for c in clusters))
        if not clusters and "is_sample" in articles and not articles.empty:
            is_sample = bool(articles["is_sample"].fillna(False).all())
    data_mode = "SAMPLE DATA" if is_sample else "LIVE"

    ordered = sorted(clusters, key=_cluster_sort_key)
    highlights = [c for c in ordered if c.get("confidence") in {"High", "Medium"}]
    watchlist = [c for c in ordered if c not in highlights]

    confidence_counts = dict(Counter(c.get("confidence", "Low") for c in clusters))
    category_counts = Counter(c.get("category", "Other") for c in clusters)
    top_categories = [name for name, _ in category_counts.most_common(3)]
    top_headline = ordered[0]["canonical_headline"] if ordered else "No deal clusters identified"
    top_category = top_categories[0] if top_categories else "No clear category"

    snapshot = {
        "deal_events": len(clusters),
        "article_count": _article_count(clusters),
        "source_count": _source_count(clusters),
        "confidence_counts": confidence_counts,
        "top_categories": top_categories,
        "auto_summary": f"Activity concentrated in {top_category}, led by {top_headline}.",
    }

    methodology = _methodology_sentences(data_mode)
    source_appendix = []
    seen_urls: set[str] = set()
    for cluster in ordered:
        for source in cluster.get("sources", []):
            url = source.get("url") or ""
            if url and url not in seen_urls:
                seen_urls.add(url)
                source_appendix.append({
                    "cluster_id": cluster.get("cluster_id", ""),
                    "label": _source_label(source),
                    "title": source.get("title", ""),
                    "url": url,
                })

    data = {
        "title": NEWSLETTER_TITLE,
        "date_range": date_range_label,
        "run_timestamp": run_timestamp.isoformat(),
        "data_mode": data_mode,
        "snapshot": snapshot,
        "highlights": highlights,
        "watchlist": watchlist,
        "methodology": methodology,
        "source_appendix": source_appendix,
    }
    markdown = _render_markdown(data)
    return markdown, data


def _render_cluster_item(cluster: dict, numbered: bool = False) -> list[str]:
    """Render one cluster as markdown lines."""
    prefix = "1. " if numbered else "- "
    companies = _companies_text(cluster.get("companies"))
    meta = (
        f"{cluster.get('deal_type', 'unknown')} | {companies} | "
        f"{cluster.get('geography', 'Not specified')} | {cluster.get('deal_value', 'undisclosed')} | "
        f"{cluster.get('confidence', 'Low')} confidence | "
        f"rel {cluster.get('relevance_score', 0)} / cred {cluster.get('credibility_score', 0)} | "
        f"{cluster.get('source_count', 0)} source(s)"
    )
    lines = [
        f"{prefix}**{cluster.get('canonical_headline', 'Untitled deal event')}**",
        f"   {meta}",
        f"   {cluster.get('why_it_matters', '')}",
    ]
    links = []
    for source in cluster.get("sources", []):
        url = source.get("url")
        if url:
            links.append(f"[{_source_label(source)}]({url})")
    if links:
        lines.append(f"   Sources: {', '.join(links)}")
    return lines


def _render_watchlist_item(cluster: dict) -> str:
    """Render one low-confidence/watchlist cluster as a compact one-liner."""
    companies = _companies_text(cluster.get("companies"))
    return (
        f"- **{cluster.get('canonical_headline', 'Untitled deal event')}** | "
        f"{cluster.get('deal_type', 'unknown')} | {companies} | "
        f"{cluster.get('deal_value', 'undisclosed')} | {cluster.get('confidence', 'Low')} | "
        f"rel {cluster.get('relevance_score', 0)} / cred {cluster.get('credibility_score', 0)} | "
        f"{cluster.get('source_count', 0)} source(s)"
    )


def _render_markdown(data: dict) -> str:
    """Render structured newsletter data into Streamlit-friendly markdown."""
    snapshot = data["snapshot"]
    lines = [
        f"# {data['title']}",
        f"**{data['data_mode']}** | {data['date_range']} | Run: {data['run_timestamp']}",
        "",
        "## Executive Snapshot",
        f"- {snapshot['deal_events']} deal event(s) from {snapshot['article_count']} article(s) across {snapshot['source_count']} source(s).",
        f"- Confidence mix: {snapshot['confidence_counts'] or {'Low': 0}}.",
        f"- Top categories: {', '.join(snapshot['top_categories']) if snapshot['top_categories'] else 'None'}.",
        f"- {snapshot['auto_summary']}",
        "",
        "## Top Deal Highlights",
    ]
    if data["highlights"]:
        for cluster in data["highlights"]:
            lines.extend(_render_cluster_item(cluster, numbered=True))
    else:
        lines.append("No high- or medium-confidence deal clusters met the current thresholds.")

    lines.extend(["", "## Watchlist"])
    if data["watchlist"]:
        for cluster in data["watchlist"]:
            lines.append(_render_watchlist_item(cluster))
    else:
        lines.append("No low-confidence/watchlist clusters.")

    lines.extend(["", "## Methodology"])
    lines.extend([f"- {sentence}" for sentence in data["methodology"]])

    lines.extend(["", "## Source Appendix"])
    if data["source_appendix"]:
        for source in data["source_appendix"]:
            lines.append(f"- {source['cluster_id']} | {source['label']} | {source['title']} | {source['url']}")
    else:
        lines.append("No cited URLs.")
    return "\n".join(lines)
