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
- [x] Constants: `GDELT_TIMEOUT=10`, `MAX_RECORDS_PER_QUERY=75`, dedupe/cluster thresholds, cutoffs `INCLUDE=70`, `WATCHLIST=55` — each with a one-line comment (plus `GDELT_SLEEP_BETWEEN=10.0` — raised from the planned 0.5s after observing erratic 429s even at 5–8s spacing)
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
- [x] Acceptance: live run returns combined DataFrame with >0 rows and correct columns (one historical run: 385 rows — 250 google_news, 75 gdelt, 60 pr_wire; GDELT counts vary run-to-run due to erratic 429 throttling, fallback sources carry the volume)
- [x] Acceptance: network failure → empty DataFrame, no exception (verified per-connector with unreachable hosts)
- [x] Acceptance: dates are timezone-aware UTC (385/385)
- [x] Commit `phase 2: ...` (5dd9509)

## Phase 3 — Cleaning & sample data (1 h)
- [x] `src/cleaning.py`: `canonicalize_url`, `normalize_title`, `extract_domain`, `clean_snippet`, `apply_cleaning`
- [x] `data/sample_articles.csv`: 14 synthetic rows per spec (3-article acquisition cluster, 2-article funding cluster, JV, PR-wire row, utm near-dupe, exact URL dupe, 2 irrelevant, 1 watchlist-grade, 1 non-FMCG bank merger), `days_ago` offsets — *14th row is a fuzzy near-dupe of article #1 ("$120 mln", token_set_ratio 96.2) so every dedup pass has sample coverage; domains spread across example.com/.org/.net so the acquisition cluster has 3 independent domains for the corroboration bonus*
- [x] `load_sample()`: shifts dates to now − N days, `is_sample=True` (in `src/sample.py`)
- [x] Acceptance: `canonicalize_url("https://www.Site.com/a/?utm_source=x&b=2&a=1#frag")` → `https://site.com/a?a=1&b=2`
- [x] Acceptance: near-dupe sample titles normalize identically (BREAKING-prefix variant ≡ original; "X Acquires Y - Reuters" suffix-strip also verified)
- [x] Acceptance: `load_sample()` → 14 rows, fresh dates (0–13d), `is_sample=True`
- [x] Commit `phase 3: ...` (249cf63)

## Phase 4 — Deduplication (1 h)
- [x] `src/dedupe.py`: 4 passes — exact URL, exact title (14d), fuzzy title ≥90 (14d), TF-IDF cosine ≥0.82 (14d, optional) — with reason codes; duplicates flagged, never deleted; duplicate_of chains resolved so every pointer targets a canonical row
- [x] Canonical selection: tier → longer snippet → newer date; stub `get_tier(domain)` reading YAML (lru_cached; Phase 5 adds name-based matching)
- [x] `data/credibility_tiers.yaml` per spec (tiers 1–3 domains + names, default tier 4)
- [x] Acceptance: utm-variant and exact-URL dupe flagged with correct reasons (both `exact_url`; utm stripped by canonicalization)
- [x] Acceptance: "BREAKING:" variant flagged — caught as `exact_url` (shares the canonical URL of article #1 by construction); the fuzzy pass is exercised by the "$120 mln" wire row → `fuzzy_title_90` (ratio 96.2)
- [x] Acceptance: canonical row is best-tier/longest-snippet (verified: wire row beat original on snippet length and became canonical); zero rows deleted (14 in → 14 out, 3 duplicates / 11 canonical); `dedupe_status` partitions cleanly; empty/single-row frames handled
- [x] Commit `phase 4: ...` (91f733f)

## Phase 5 — Relevance & credibility scoring (1.5 h)
- [x] `src/scoring.py`: `score_relevance(row)` — additive weights table per spec with reason codes, penalties, no-deal-term cap at 30 (all weights as commented module constants; word-boundary regex matching; targeted query families = food_beverage / beauty_personal_care / pe_funding)
- [x] Classification: ≥70 include, 55–69 watchlist, <55 reject → `status` (`score_articles` scores everything incl. duplicates so Transparency can show reasons)
- [x] `get_tier(domain, source_name)`: domain first, then case-insensitive name match (Google News rows), cached YAML — dedupe's stub now delegates here (single loader)
- [x] `score_credibility(row)`: tier base, +5 date+name present, −10 no date, clamp 0–100
- [x] Cluster credibility: `0.60·max + 0.25·avg_top3 + corroboration_bonus` (cap 100); single-source quirk documented in docstring (lone Reuters 90 → 76)
- [x] Confidence label rules (High/Medium/Low)
- [x] Acceptance: sample acquisition 75 `[strong_deal_term, fmcg_term, recent_7d, deal_value_visible]`; earnings 0 with `earnings_noise`
- [x] Acceptance: bank merger rejected at 35 (strong term + recency only — no FMCG/category signal; contingency cap rule not needed)
- [x] Acceptance: reuters.com → (1, 90); unknownblog.net → (4, 45); name "Reuters" on foreign domain → tier 1 (case-insensitive); sample PR row tier_3 via name fallback
- [x] Commit `phase 5: ...` (5ee149b)

## Phase 6 — Deal clustering (1.5 h)
- [x] `src/clustering.py`: greedy single-pass over include/watchlist canonicals — join if dates ≤30d AND (cosine ≥0.6 OR (shared company AND cosine ≥0.4)); comment on order-dependence
- [x] Company extraction: watchlist match (accent-insensitive) + capitalized 1–3 token phrases, generic-word stoplist
- [x] Cluster record: canonical_headline, deal_type priority order, companies (max 5), geography, category buckets, deal_value regex, scores, confidence, deterministic `why_it_matters` template, sources list, counts/dates
- [x] Acceptance: sample → 1 acquisition cluster (4 canonical articles after dedup, reflecting the PR + wire corroboration rows), 1 funding (2), 1 JV (1), plus 1 watchlist sale/exploration cluster
- [x] Acceptance: acquisition and funding stories never mix
- [x] Acceptance: visible deal values extracted verbatim (`$120 million`, `$50 million`, `$40M`); watchlist cluster is `undisclosed`; `why_it_matters` uses only derived fields
- [x] Commit `phase 6: ...`

## Phase 7 — Newsletter & exports (1.5 h)
- [x] `src/newsletter.py`: header with LIVE/SAMPLE badge, executive snapshot, top deal highlights (sorted by confidence/relevance/credibility/recency), compact watchlist one-liners, 4-sentence methodology, source appendix — markdown + structured dict
- [x] `src/exports.py`: raw + processed CSV/JSON, `deal_clusters.json`, `newsletter.docx`, `newsletter.xlsx` (7 sheets: Executive Snapshot, Deal Highlights, Deal Clusters, Article Evidence, Watchlist, Rejected & Duplicates, Methodology; frozen headers, widths, autofilters)
- [x] All export functions return file paths AND bytes for Streamlit downloads (`ExportArtifact`)
- [x] Acceptance: markdown renders cleanly; docx opens with correct headings; xlsx is a valid 7-sheet workbook (verified via zip structure because `openpyxl` is intentionally not in requirements)
- [x] Acceptance: sample run shows SAMPLE DATA badge in newsletter and docx; empty non-sample run defaults to LIVE
- [x] Sub-agent audit: first pass found empty-frame mode inference + verbose watchlist; both fixed; second pass GREEN LIGHT
- [x] Commit `phase 7: ...`

## Phase 8 — Streamlit app (2 h)
- [x] `src/pipeline.py`: `run_pipeline(...)` → PipelineResult; exact order: fetch → clean → dedupe → score → count includes → fallback (<5, replace entirely) → thresholds (display/clustering filter only) → cluster → newsletter → exports
- [x] Cache the fetch layer (`st.cache_data(ttl=1800)`), not the whole pipeline
- [x] Sidebar: date range (24h/7d/30d), relevance slider (default 60), credibility slider (default 50), sample-fallback checkbox (default on), Run button, sample-mode warning banner
- [x] Tab 1 Snapshot & Newsletter: metric row + rendered newsletter + 4 download buttons
- [x] Tab 2 Deal Clusters: expander per cluster with evidence table (LinkColumn)
- [x] Tab 3 Data & Transparency: duplicates table, rejected table, searchable processed dataframe
- [x] Tab 4 Methodology: mermaid source + dedup/scoring/tiers/limitations explanation
- [x] Acceptance: no keys/no live rows/fetch failure → sample mode end to end; with internet → live data through all tabs (verified 385 live rows, 7 canonical includes, LIVE mode)
- [x] Acceptance: threshold changes alter cluster count without changing fetch layer semantics; downloads produce valid files
- [x] Sub-agent audit: first pass found regex search + direct fetch failure handling; both fixed; second pass GREEN LIGHT
- [x] Commit `phase 8: ...`

## Phase 9 — Tests (1 h)
- [x] `tests/test_pipeline.py`, 13 no-network tests: canonicalize_url, normalize_title, exact URL dupes, fuzzy ≥90 / not <90, acquisition → include, earnings → reject with reason, no-deal-term cap ≤30, tier lookup (domain/unknown/name), clustering groups vs separates, sample loader (14 rows, fresh dates, is_sample), pipeline fallback/no-mixing, threshold behavior, empty-live/no-fallback
- [x] Acceptance: `.venv/bin/python -m pytest -q` green in <10s (13 passed in 1.66s locally; audit rerun also green)
- [x] Sub-agent audit: GREEN LIGHT; socket-blocked audit run confirmed no hidden network calls
- [x] Commit `phase 9: ...`

## Phase 10 — README, diagram, deploy (1.5 h)
- [x] README per spec order: pitch (positioning line verbatim) · links · why clustering · source strategy · architecture diagram · pipeline walkthrough · dedup logic · relevance weights table · credibility (incl. single-source quirk) · run locally · outputs · honest assumptions & limitations · future enhancements
- [x] `docs/architecture.mmd` with the mermaid from PLAN.md
- [x] Final-acceptance prep: Streamlit download row exposes raw CSV and raw JSON, plus DOCX/XLSX and clusters JSON
- [x] Local verification: `.venv/bin/python -m pytest -q` green; no-network sample smoke produced required exports
- [x] Sub-agent audit: first pass GREEN LIGHT for docs/diagram; second pass GREEN LIGHT after raw JSON download addition
- [ ] Push to public GitHub; deploy to Streamlit Community Cloud; verify cold start + sample mode
- [ ] Final acceptance: demo link works in incognito; repo has README/diagram/tests/no secrets; raw CSV/JSON downloadable; DOCX + XLSX newsletter; dedup+relevance explained in README and visible in Transparency tab; non-technical reader can skim newsletter in <2 min
- [x] Commit `phase 10: ...`

## Schedule
- Day 1: Phases 0–5 — scored, deduped articles from live + sample data
- Day 2: Phases 6–8 — full app running end to end locally
- Day 3: Phases 9–10 + buffer — tests, README, deploy, demo dry-run
