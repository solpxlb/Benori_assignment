# DealLens FMCG — Task Checklist

Derived from PLAN.md. Work one phase at a time; do not start a phase until the previous phase's acceptance checks pass. Commit per phase: `phase N: <summary>`.

## Phase 0 — Scaffold & de-risk (30–45 min)
- [x] Create repo skeleton: `app.py` (st.title placeholder), `README.md`, `requirements.txt`, `.gitignore`, `docs/architecture.mmd`, `data/`, `outputs/.gitkeep`, `src/__init__.py`, `src/config.py`, `tests/__init__.py`
- [x] Write `requirements.txt` (streamlit, pandas, requests, feedparser, rapidfuzz, scikit-learn, tldextract, pyyaml, python-docx, XlsxWriter, pytest)
- [x] Write throwaway `scripts/probe_sources.py`: probe GDELT DOC 2.0, Google News RSS, PR-wire RSS feeds
- [x] Record probe findings as comment block (counts, JSON quirks, date formats); note which PR-wire feeds are alive (decision gate for Phase 2 rss_feeds connector) — *findings: quote only multi-word phrases, `sourcelang:english` required, rate limit 1 req/5s; live feeds: prnewswire-consumer, globenewswire-ma, globenewswire-consumer; businesswire dead*
- [x] Acceptance: `pip install -r requirements.txt` succeeds
- [x] Acceptance: probe prints real titles from both sources (or documents failures exactly)
- [x] Acceptance: `streamlit run app.py` shows placeholder
- [x] Commit `phase 0: ...` (4a07c3a)

## Phase 1 — Config, queries, data model (45 min)
- [x] `src/config.py`: term lists verbatim (FMCG_TERMS, CATEGORY_TERMS, STRONG/SOFT_DEAL_TERMS, COMPANY_WATCHLIST, NEGATIVE_TERMS)
- [x] 5 query families, each as GDELT-syntax string + plain-text RSS variant (general, F&B, beauty/personal care, PE/funding, watchlist)
- [x] Constants: `GDELT_TIMEOUT=10`, `MAX_RECORDS_PER_QUERY=75`, dedupe/cluster thresholds, cutoffs `INCLUDE=70`, `WATCHLIST=55` — each with a one-line comment (plus `GDELT_SLEEP_BETWEEN=5.0` from probe finding)
- [x] `src/models.py`: plain dataclass `Article` with full schema + `to_dict()`
- [x] Acceptance: `from src.config import *` works, all lists non-empty
- [x] Acceptance: query builder produces 5 valid GDELT query strings (parenthesized ORs, quoted phrases)
- [x] Commit `phase 1: ...` (ceca582)

## Phase 2 — Ingestion (1.5 h)
- [x] `src/sources/__init__.py` exporting `fetch_all`
- [x] `src/sources/gdelt.py`: `fetch_gdelt(...)` — defensive JSON parse, `seendate` → UTC datetime, `snippet=""`, 10s sleep between queries (Phase 2 finding: GDELT 429s even at 8s spacing under sustained use; partial results are normal and handled gracefully)
- [x] `src/sources/google_news.py`: `fetch_google_news(...)` — feedparser, `source.title` as source name, strip HTML from summary
- [x] `src/sources/rss_feeds.py`: built for the 3 feeds the probe confirmed alive (prnewswire-consumer, globenewswire-ma, globenewswire-consumer); Business Wire dead → README mention only
- [x] `fetch_all(timespan)`: concat, assign `article_id` (uuid4 hex[:12]), drop empty title/url rows
- [x] Acceptance: live run returns combined DataFrame with >0 rows and correct columns (385 rows: 250 google_news, 75 gdelt, 60 pr_wire)
- [x] Acceptance: network failure → empty DataFrame, no exception (verified per-connector with unreachable hosts)
- [x] Acceptance: dates are timezone-aware UTC (385/385)
- [x] Commit `phase 2: ...` (5dd9509)

## Phase 3 — Cleaning & sample data (1 h)
- [ ] `src/cleaning.py`: `canonicalize_url`, `normalize_title`, `extract_domain`, `clean_snippet`, `apply_cleaning`
- [ ] `data/sample_articles.csv`: 14 synthetic rows per spec (3-article acquisition cluster, 2-article funding cluster, JV, PR-wire row, utm near-dupe, exact URL dupe, 2 irrelevant, 1 watchlist-grade, 1 non-FMCG bank merger), `days_ago` offsets
- [ ] `load_sample()`: shifts dates to now − N days, `is_sample=True`
- [ ] Acceptance: `canonicalize_url("https://www.Site.com/a/?utm_source=x&b=2&a=1#frag")` → `https://site.com/a?a=1&b=2`
- [ ] Acceptance: near-dupe sample titles normalize identically
- [ ] Acceptance: `load_sample()` → 14 rows, fresh dates, `is_sample=True`
- [ ] Commit `phase 3: ...`

## Phase 4 — Deduplication (1 h)
- [ ] `src/dedupe.py`: 4 passes — exact URL, exact title (14d), fuzzy title ≥90 (14d), TF-IDF cosine ≥0.82 (14d, optional) — with reason codes; duplicates flagged, never deleted
- [ ] Canonical selection: tier → longer snippet → newer date; stub `get_tier(domain)` reading YAML
- [ ] `data/credibility_tiers.yaml` per spec (tiers 1–3 domains + names, default tier 4)
- [ ] Acceptance: utm-variant and exact-URL dupe flagged with correct reasons
- [ ] Acceptance: "BREAKING:" variant flagged `fuzzy_title_90` or `exact_title_14d`
- [ ] Acceptance: canonical row is best-tier/longest-snippet; zero rows deleted; `dedupe_status` partitions cleanly
- [ ] Commit `phase 4: ...`

## Phase 5 — Relevance & credibility scoring (1.5 h)
- [ ] `src/scoring.py`: `score_relevance(row)` — additive weights table per spec with reason codes, penalties, no-deal-term cap at 30
- [ ] Classification: ≥70 include, 55–69 watchlist, <55 reject → `status`
- [ ] `get_tier(domain, source_name)`: domain first, then case-insensitive name match (Google News rows), cached YAML
- [ ] `score_credibility(row)`: tier base, +5 date+name present, −10 no date, clamp 0–100
- [ ] Cluster credibility: `0.60·max + 0.25·avg_top3 + corroboration_bonus` (cap 100); document single-source quirk
- [ ] Confidence label rules (High/Medium/Low)
- [ ] Acceptance: sample acquisition ≥70 with correct reasons; earnings <55 with `earnings_noise`
- [ ] Acceptance: bank merger rejected (if it sneaks through, add "no FMCG signal → cap 50" and document)
- [ ] Acceptance: reuters.com → tier 1/90; unknownblog.net → tier 4/45; name "Reuters" → tier 1
- [ ] Commit `phase 5: ...`

## Phase 6 — Deal clustering (1.5 h)
- [ ] `src/clustering.py`: greedy single-pass over include/watchlist canonicals — join if dates ≤30d AND (cosine ≥0.6 OR (shared company AND cosine ≥0.4)); comment on order-dependence
- [ ] Company extraction: watchlist match (accent-insensitive) + capitalized 1–3 token phrases, generic-word stoplist
- [ ] Cluster record: canonical_headline, deal_type priority order, companies (max 5), geography, category buckets, deal_value regex, scores, confidence, deterministic `why_it_matters` template, sources list, counts/dates
- [ ] Acceptance: sample → 1 acquisition cluster (3 articles, or 2 post-dedup — verify), 1 funding (2), 1 JV (1)
- [ ] Acceptance: acquisition and funding stories never mix
- [ ] Acceptance: $40M extracted for funding sample, "undisclosed" elsewhere; why_it_matters has zero non-derivable info
- [ ] Commit `phase 6: ...`

## Phase 7 — Newsletter & exports (1.5 h)
- [ ] `src/newsletter.py`: header with LIVE/SAMPLE badge, executive snapshot, top deal highlights (sorted by confidence/relevance/credibility/recency), watchlist one-liners, 4-sentence methodology, source appendix — markdown + structured dict
- [ ] `src/exports.py`: raw + processed CSV/JSON, `deal_clusters.json`, `newsletter.docx`, `newsletter.xlsx` (7 sheets: Executive Snapshot, Deal Highlights, Deal Clusters, Article Evidence, Watchlist, Rejected & Duplicates, Methodology; frozen headers, widths, autofilters)
- [ ] All export functions return file paths AND bytes/BytesIO for Streamlit downloads
- [ ] Acceptance: markdown renders cleanly in Streamlit; docx opens with correct headings; xlsx opens populated
- [ ] Acceptance: sample run shows SAMPLE DATA badge in newsletter and docx
- [ ] Commit `phase 7: ...`

## Phase 8 — Streamlit app (2 h)
- [ ] `src/pipeline.py`: `run_pipeline(...)` → PipelineResult; exact order: fetch → clean → dedupe → score → count includes → fallback (<5, replace entirely) → thresholds (display filter only) → cluster → newsletter → exports
- [ ] Cache the fetch layer (`st.cache_data(ttl=1800)`), not the whole pipeline
- [ ] Sidebar: date range (24h/7d/30d), relevance slider (default 60), credibility slider (default 50), sample-fallback checkbox (default on), Run button, sample-mode warning banner
- [ ] Tab 1 Snapshot & Newsletter: metric row + rendered newsletter + 4 download buttons
- [ ] Tab 2 Deal Clusters: expander per cluster with evidence table (LinkColumn)
- [ ] Tab 3 Data & Transparency: duplicates table, rejected table, searchable processed dataframe
- [ ] Tab 4 Methodology: mermaid source + dedup/scoring/tiers/limitations explanation
- [ ] Acceptance: no keys, no internet → sample mode end to end; with internet → live data through all tabs
- [ ] Acceptance: sliders re-score without re-fetching; downloads produce valid files
- [ ] Commit `phase 8: ...`

## Phase 9 — Tests (1 h)
- [ ] `tests/test_pipeline.py`, ~10 tests, no network: canonicalize_url, normalize_title, exact URL dupes, fuzzy ≥90 / not <90, acquisition → include, earnings → reject with reason, no-deal-term cap ≤30, tier lookup (domain/unknown/name), clustering groups vs separates, sample loader (14 rows, fresh dates, is_sample)
- [ ] Acceptance: `pytest -q` green in <10s
- [ ] Commit `phase 9: ...`

## Phase 10 — README, diagram, deploy (1.5 h)
- [ ] README per spec order: pitch (positioning line verbatim) · links · why clustering · source strategy · architecture diagram · pipeline walkthrough · dedup logic · relevance weights table · credibility (incl. single-source quirk) · run locally · outputs · honest assumptions & limitations · future enhancements
- [ ] `docs/architecture.mmd` with the mermaid from PLAN.md
- [ ] Push to public GitHub; deploy to Streamlit Community Cloud; verify cold start + sample mode
- [ ] Final acceptance: demo link works in incognito; repo has README/diagram/tests/no secrets; raw CSV/JSON downloadable; DOCX + XLSX newsletter; dedup+relevance explained in README and visible in Transparency tab; non-technical reader can skim newsletter in <2 min
- [ ] Commit `phase 10: ...`

## Schedule
- Day 1: Phases 0–5 — scored, deduped articles from live + sample data
- Day 2: Phases 6–8 — full app running end to end locally
- Day 3: Phases 9–10 + buffer — tests, README, deploy, demo dry-run
