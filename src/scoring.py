"""Relevance & credibility scoring. Every score returns reason codes.

All weights and thresholds are module-level constants with one-line comments —
they are the assumptions of this system and must be inspectable and defensible.
Scores use only text actually present in the row (no-hallucination policy).
"""

import functools
import re
from pathlib import Path

import pandas as pd
import yaml

from src.config import (FMCG_TERMS, CATEGORY_TERMS, STRONG_DEAL_TERMS, SOFT_DEAL_TERMS,
                        COMPANY_WATCHLIST, QUERY_FAMILIES, INCLUDE, WATCHLIST)

# ---------------------------------------------------------------------------
# Relevance weights (additive, clamped 0-100)
# ---------------------------------------------------------------------------

W_STRONG_DEAL = 30        # strong deal term (acquisition/merger/...) in title or snippet
W_SOFT_DEAL = 20          # soft deal term (funding/stake/...) — only when no strong term
W_FMCG_TERM = 25          # direct FMCG/CPG term present
W_CATEGORY_TERM = 15      # category term (food/beauty/...) — only when no direct FMCG term, once
W_WATCHLIST = 15          # known FMCG company from the watchlist mentioned
W_RECENT_7D = 10          # published within 7 days
W_RECENT_30D = 5          # published 8-30 days ago
W_DEAL_VALUE = 10         # a monetary deal value is visible in the text
W_QUERY_CONTEXT = 5       # returned by a tightly-targeted query family (offsets GDELT's missing snippets)
P_EARNINGS_NOISE = -25    # earnings/stock/dividend noise without a strong deal term
P_LAUNCH_NOISE = -10      # product-launch/marketing noise without any deal term
NO_DEAL_TERM_CAP = 30     # hard cap when no deal term at all is present

# Credibility tier base scores (tier number -> base credibility)
TIER_BASE_SCORES = {1: 90, 2: 75, 3: 60, 4: 45}
CRED_METADATA_BONUS = 5   # both published date and source name present
CRED_NO_DATE_PENALTY = -10  # no published date

# Cluster credibility weights (Phase 6 calls cluster_credibility)
CLUSTER_W_MAX = 0.60      # weight on the single most credible article
CLUSTER_W_TOP3 = 0.25     # weight on the average of the top-3 article credibilities
CORROB_2_DOMAINS = 10     # >=2 independent domains corroborate
CORROB_3_DOMAINS = 15     # >=3 independent domains corroborate (replaces the +10)
CORROB_OFFICIAL_MIX = 15  # a tier_1 source AND a tier_3 PR source corroborate (official + independent)

# Confidence label thresholds
CONF_HIGH_RELEVANCE = 75  # High requires relevance >= this ...
CONF_HIGH_CRED = 75       # ... and credibility >= this, plus >=2 domains or a tier_1 source
CONF_MED_RELEVANCE = 65   # Medium requires relevance >= this ...
CONF_MED_CRED = 60        # ... and credibility >= this

# NEGATIVE_TERMS from config, split by which penalty they trigger:
EARNINGS_NOISE_TERMS = ["earnings", "stock price", "share buyback", "dividend",
                        "quarterly results", "crypto", "ETF", "real estate"]
LAUNCH_NOISE_TERMS = ["product launch", "marketing campaign"]

# Query families specific enough that membership itself is a topical signal
# (category- or thesis-targeted; compensates for GDELT artlist having no snippets).
TARGETED_QUERY_FAMILIES = {"food_beverage", "beauty_personal_care", "pe_funding"}

# Deal-value patterns: currency symbol+digit, number+magnitude word, currency code.
DEAL_VALUE_PATTERNS = [
    re.compile(r"[$€£₹]\s?\d"),
    re.compile(r"\b\d+\s?(million|billion|crore|mn|bn)\b", re.IGNORECASE),
    re.compile(r"\b(USD|INR|EUR)\b"),
]

TIERS_YAML = Path(__file__).resolve().parent.parent / "data" / "credibility_tiers.yaml"


def _term_pattern(terms: list[str]) -> re.Pattern:
    """Compile a case-insensitive word-boundary regex matching any of `terms`."""
    alternation = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)

_STRONG_RE = _term_pattern(STRONG_DEAL_TERMS)
_SOFT_RE = _term_pattern(SOFT_DEAL_TERMS)
_FMCG_RE = _term_pattern(FMCG_TERMS)
_CATEGORY_RE = _term_pattern(CATEGORY_TERMS)
_WATCHLIST_RE = _term_pattern(COMPANY_WATCHLIST)
_EARNINGS_RE = _term_pattern(EARNINGS_NOISE_TERMS)
_LAUNCH_RE = _term_pattern(LAUNCH_NOISE_TERMS)

# Map every query representation (family name, GDELT string, RSS string) -> family name.
_QUERY_TO_FAMILY: dict = {}
for _f in QUERY_FAMILIES:
    _QUERY_TO_FAMILY[_f["name"]] = _f["name"]
    _QUERY_TO_FAMILY[_f["gdelt"]] = _f["name"]
    _QUERY_TO_FAMILY[_f["rss"]] = _f["name"]


def query_family(query: str) -> str:
    """Resolve a row's query value (family name or raw query string) to a family name."""
    return _QUERY_TO_FAMILY.get(query or "", "")


def score_relevance(row: pd.Series) -> tuple[int, list[str]]:
    """Score 0-100 how likely a row is a real FMCG deal/investment story.

    Additive signals with reason codes; penalties for earnings/launch noise;
    hard cap at NO_DEAL_TERM_CAP when no deal language is present at all.
    """
    text = f"{row.get('title', '')} {row.get('snippet', '')}"
    score, reasons = 0, []

    has_strong = bool(_STRONG_RE.search(text))
    has_soft = bool(_SOFT_RE.search(text))
    if has_strong:
        score += W_STRONG_DEAL
        reasons.append("strong_deal_term")
    elif has_soft:
        score += W_SOFT_DEAL
        reasons.append("soft_deal_term")

    if _FMCG_RE.search(text):
        score += W_FMCG_TERM
        reasons.append("fmcg_term")
    elif _CATEGORY_RE.search(text):
        score += W_CATEGORY_TERM  # max one category bucket by design
        reasons.append("category_term")

    if _WATCHLIST_RE.search(text):
        score += W_WATCHLIST
        reasons.append("watchlist_company")

    published = row.get("published_at")
    retrieved = row.get("retrieved_at")
    if pd.notna(published) and pd.notna(retrieved):
        age = retrieved - published
        if age <= pd.Timedelta(days=7):
            score += W_RECENT_7D
            reasons.append("recent_7d")
        elif age <= pd.Timedelta(days=30):
            score += W_RECENT_30D
            reasons.append("recent_30d")

    if any(p.search(text) for p in DEAL_VALUE_PATTERNS):
        score += W_DEAL_VALUE
        reasons.append("deal_value_visible")

    if query_family(row.get("query", "")) in TARGETED_QUERY_FAMILIES:
        score += W_QUERY_CONTEXT
        reasons.append("source_query_context")

    if _EARNINGS_RE.search(text) and not has_strong:
        score += P_EARNINGS_NOISE
        reasons.append("earnings_noise")
    if _LAUNCH_RE.search(text) and not has_strong and not has_soft:
        score += P_LAUNCH_NOISE
        reasons.append("launch_noise")

    if not has_strong and not has_soft:
        score = min(score, NO_DEAL_TERM_CAP)
        reasons.append("no_deal_term")

    return max(0, min(100, score)), reasons


def classify(relevance: int) -> str:
    """Map a relevance score to include / watchlist / reject."""
    if relevance >= INCLUDE:
        return "include"
    if relevance >= WATCHLIST:
        return "watchlist"
    return "reject"


@functools.lru_cache(maxsize=1)
def _load_tiers(path: str = str(TIERS_YAML)) -> dict:
    """Load and cache the credibility tiers YAML."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_tier(domain: str, source_name: str = "") -> tuple[int, int]:
    """Return (tier number, base credibility) for a source.

    Domain match first; case-insensitive source-name match as fallback (needed
    for Google News rows, whose links hide the real domain); else default tier.
    """
    tiers = _load_tiers()
    domain = (domain or "").lower()
    name = (source_name or "").strip().lower()
    for tier_no in (1, 2, 3):
        if domain in tiers.get(f"tier_{tier_no}", {}).get("domains", []):
            return tier_no, TIER_BASE_SCORES[tier_no]
    for tier_no in (1, 2, 3):
        names = [n.lower() for n in tiers.get(f"tier_{tier_no}", {}).get("names", [])]
        if name and name in names:
            return tier_no, TIER_BASE_SCORES[tier_no]
    default = tiers.get("default_tier", 4)
    return default, TIER_BASE_SCORES[default]


def score_credibility(row: pd.Series) -> tuple[int, str, list[str]]:
    """Score 0-100 source credibility for one article, with tier label and reasons."""
    tier, base = get_tier(row.get("domain", ""), row.get("source_name", ""))
    score = base
    reasons = [f"tier_{tier}_base_{base}"]
    has_date = pd.notna(row.get("published_at"))
    if has_date and (row.get("source_name") or "").strip():
        score += CRED_METADATA_BONUS
        reasons.append("metadata_complete")
    if not has_date:
        score += CRED_NO_DATE_PENALTY
        reasons.append("no_published_date")
    return max(0, min(100, score)), f"tier_{tier}", reasons


def score_articles(df: pd.DataFrame) -> pd.DataFrame:
    """Apply relevance + credibility scoring to every row (duplicates included).

    Everything is scored so the Transparency tab can show rejected/duplicate rows
    with reasons; aggregates and clustering later exclude duplicates themselves.
    """
    df = df.copy()
    if df.empty:
        return df
    rel = df.apply(score_relevance, axis=1)
    df["relevance_score"] = [r[0] for r in rel]
    df["relevance_reasons"] = [r[1] for r in rel]
    df["status"] = df["relevance_score"].map(classify)
    cred = df.apply(score_credibility, axis=1)
    df["credibility_score"] = [c[0] for c in cred]
    df["credibility_tier"] = [c[1] for c in cred]
    df["credibility_reasons"] = [c[2] for c in cred]
    return df


def cluster_credibility(credibilities: list[int], domains: list[str],
                        tier_labels: list[str]) -> int:
    """Credibility of a deal-event cluster from its member articles' scores.

    0.60·max + 0.25·avg(top 3) + corroboration bonus, capped at 100.
    Known quirk (intentional, documented in methodology): a single-source cluster
    scores BELOW its own article's credibility (lone Reuters 90 -> ~76) — one
    source is less certain than that source is credible; corroboration earns it back.
    """
    if not credibilities:
        return 0
    ranked = sorted(credibilities, reverse=True)
    top3 = ranked[:3]
    score = CLUSTER_W_MAX * ranked[0] + CLUSTER_W_TOP3 * (sum(top3) / len(top3))
    n_domains = len({d for d in domains if d})
    if n_domains >= 3:
        score += CORROB_3_DOMAINS
    elif n_domains >= 2:
        score += CORROB_2_DOMAINS
    if "tier_1" in tier_labels and "tier_3" in tier_labels:
        score += CORROB_OFFICIAL_MIX
    return min(100, round(score))


def confidence_label(relevance: int, credibility: int, n_independent_domains: int,
                     has_tier1: bool) -> str:
    """High / Medium / Low confidence label for a cluster."""
    if (relevance >= CONF_HIGH_RELEVANCE and credibility >= CONF_HIGH_CRED
            and (n_independent_domains >= 2 or has_tier1)):
        return "High"
    if relevance >= CONF_MED_RELEVANCE and credibility >= CONF_MED_CRED:
        return "Medium"
    return "Low"
