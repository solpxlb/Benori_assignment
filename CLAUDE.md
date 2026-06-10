# CLAUDE.md — DealLens FMCG

This file is standing context for every Claude Code session on this repo. Read it before doing anything.

**Naming:** the app is **DealLens FMCG**; the newsletter it generates is titled **FMCG DealBrief**.

## What this project is

A take-home assignment for a job application at Benori (research/intelligence firm). I have ~3 days. The deliverable is a Streamlit demo app + public GitHub repo that generates an FMCG industry intelligence newsletter on recent M&A and investment activity from publicly available news.

**Benori's exact evaluation criteria (from their brief):**
1. Clear, minimal pipeline/"agent" thinking: ingestion → cleaning → scoring → newsletter
2. Practical handling of de-duplication and relevance
3. Sensible credibility and transparent assumptions
4. A newsletter a business user could realistically skim to understand FMCG deal activity

**Their required outputs:** demo app link (Streamlit), GitHub link with architecture diagram + README, raw data in CSV/JSON, brief written explanation of dedup + relevance logic, structured newsletter in Excel/Word format.

Every implementation decision should optimize for these four criteria. When in doubt: simpler + more explainable beats cleverer. I will be grilled on this code in an interview — I must be able to defend every threshold and design choice from memory.

## The one differentiating idea

Do NOT summarize individual articles. Cluster articles into **deal events** — one cluster = one transaction, with multiple corroborating sources, a relevance score, a credibility score (boosted by independent corroboration), and a confidence label. This is the product's identity.

## Execution model

Work happens in phases defined in `PLAN.md`. Rules:
- Build ONLY the current phase. Do not start the next phase, do not "while I'm here" anything.
- Each phase ends with its acceptance checks. State which checks pass and how you verified. The human runs them too before approving the next phase.
- Commit message per phase: `phase N: <summary>`.

## Standing rules (never violate)

- Python 3.11. Streamlit. No paid APIs. The app must run with **zero API keys and zero internet** (sample mode).
- Every network call: try/except, timeout=10, return empty list/DataFrame on failure, surface a warning, never crash.
- **No hallucination policy:** deal values, company names, geographies, dates come only from text actually present in fetched data. Unknown → "undisclosed" / "not specified". The why-it-matters text is a deterministic template, never generative.
- Sample data is always visibly labeled (is_sample=True, UI banner, newsletter badge). Never mix sample and live data in one run — sample fallback **replaces** the live set entirely when live yields <5 included articles.
- All scoring weights and thresholds are named constants at module top, with a one-line comment each. They must be inspectable and editable.
- Typed signatures + docstrings on public functions. No async, no retries-with-backoff, no pydantic, no LLM calls, no agent frameworks. If a library beyond requirements.txt seems needed, stop and ask.
- No secrets in repo. No paywall bypassing. No full-article scraping — headlines + RSS snippets only.

## Known technical gotchas (learned in Phase 0 probing — update this section with actual findings)

- **GDELT DOC 2.0**: endpoint `https://api.gdeltproject.org/api/v2/doc/doc`. Query syntax: OR inside parentheses, space = implicit AND. **Quote ONLY multi-word phrases — quoting a single short word (e.g. `"FMCG"`, `"food"`) returns HTTP 200 + text/html + "The specified phrase is too short."** Append `sourcelang:english` to every query, else results are dominated by non-English articles. Verified working query:
  `(acquisition OR merger OR "stake sale") (FMCG OR "consumer goods" OR food OR beverage) sourcelang:english`
  Params: `mode=artlist&format=json&maxrecords=75&timespan=7d&sort=datedesc`. Error pages come back with HTTP 200 — check content-type / wrap json parse; status code is not a health signal. Articles live under the `articles` key; per-article keys: `domain, language, seendate, socialimage, sourcecountry, title, url, url_mobile` (no snippet in artlist mode). Dates are `seendate` in `YYYYMMDDTHHMMSSZ` (UTC). **Rate limiting is erratic: documented as 1 req/5s, but Phase 2 testing showed 429s (content-type None, plain-text body) even at 8–10s spacing, hitting different queries on different runs — there appears to be a stickier per-IP budget under sustained use. Sleep 10s between queries and treat partial GDELT results as normal: the connector degrades gracefully, Google News provides volume, and the sample fallback covers total failure.** Occasional SSL-handshake read timeouts seen — timeout=10 + try/except mandatory.
- **PR-wire feeds (Phase 0 probe, 2026-06-10)**: ALIVE — prnewswire consumer-products-retail category feed, globenewswire M&A subject feed, globenewswire consumer-products industry feed (exact URLs in `scripts/probe_sources.py`). DEAD — businesswire RSS (HTTP 200 but 0 entries). Phase 2 builds the rss_feeds connector for the three live feeds; Business Wire is README-mention only.
- **Google News RSS**: `https://news.google.com/rss/search?q={quote_plus(query)}&hl=en-US&gl=US&ceid=US:en`. Links are google redirect URLs, so the article's real domain is NOT recoverable from the link — use `entry.source.title` as source name and match credibility tiers by **name**, not domain, for these rows. Snippet from `entry.summary` (contains HTML — strip it).
- **Corroboration counting (Phase 6)**: independent-source counts must use `scoring.source_key(row)` (publisher name for Google News rows, registered domain otherwise) — never raw `domain`, or every Google News row collapses into "google.com" and clusters lose their corroboration bonus.
- Streamlit cache: cache the **fetch** layer (`st.cache_data(ttl=1800)`), not the whole pipeline, so threshold-slider changes re-score instantly without re-fetching.
- Sample CSV stores `days_ago` offsets, not absolute dates; the loader computes `published_at = now - days_ago` so demos never look stale.

## Interaction with thresholds (pre-decided, don't re-litigate)

- **Exact pipeline order:** fetch live → clean → dedupe → score → count canonical `include` rows → if <5 and fallback enabled, discard live entirely, load sample, re-run clean/dedupe/score → apply sidebar thresholds (display/clustering filter only) → cluster → newsletter → exports. The fallback check is POST-scoring — you cannot count includes before scoring.
- Relevance sliders filter which clusters DISPLAY and which articles enter clustering; scoring itself always runs on everything so the Transparency tab can show rejected items with reasons.
- Dedup is 4 passes: exact URL → exact title (14d) → fuzzy title ≥90 (14d) → TF-IDF cosine ≥0.82 (14d, optional, mainly catches snippet-bearing rows since GDELT artlist has no snippets). Duplicate rows are kept and flagged, excluded from scoring aggregates and clustering, shown in the Transparency tab.
- Cluster credibility = `0.60·max_article_cred + 0.25·avg_top3_cred + corroboration_bonus` (cap 100). Single-source clusters scoring below their own article's credibility is intentional (one source = less certain) — say so in methodology.
- PR-wire RSS connector is built ONLY for feeds the Phase 0 probe confirmed alive. Dead feeds → README source-strategy mention only, no code.

## Definition of done (overall)

`streamlit run app.py` on a fresh clone with no keys: sample mode produces a complete newsletter, all 4 tabs populated, DOCX/XLSX/CSV/JSON downloads work, `pytest -q` green, README explains architecture + dedup + scoring + limitations honestly. Deployed on Streamlit Community Cloud.