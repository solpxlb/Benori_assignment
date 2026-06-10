"""Deal-event clustering: greedy single-pass grouping of articles into transactions.

One cluster = one deal event, with corroborating sources, aggregated relevance/
credibility, and an evidence-bounded why-it-matters line. Greedy is order-dependent
(articles walked newest-first, compared against each cluster's seed article);
acceptable at this scale and documented in limitations.
"""

import re
import unicodedata

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.config import (COMPANY_WATCHLIST, CATEGORY_TERMS,
                        CLUSTER_COSINE_THRESHOLD, CLUSTER_COSINE_WITH_COMPANY,
                        CLUSTER_DATE_WINDOW_DAYS)
from src.scoring import cluster_credibility, confidence_label, source_key

# Capitalized phrases of 1-3 tokens in raw titles are company-name candidates.
_CAP_PHRASE_RE = re.compile(r"\b[A-Z][a-zA-Z&'.-]+(?:\s[A-Z][a-zA-Z&'.-]+){0,2}\b")

# Generic capitalized words that are not company names: phrase candidates are cut
# at the first stoplisted token ("Example Foods Completes" -> "Example Foods").
_PHRASE_STOPLIST = {
    "the", "this", "that", "these", "those", "a", "an", "and", "or", "but",
    "in", "on", "at", "of", "for", "with", "from", "to", "by", "as", "its",
    "why", "how", "what", "when", "where", "who", "which",
    "breaking", "update", "exclusive", "report", "latest", "news", "new", "says",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "acquisition", "acquires", "acquired", "acquire", "merger", "merges",
    "buyout", "takeover", "divests", "divestment", "deal", "sale", "stake",
    "completes", "announces", "raises", "secures", "launches", "forms",
    "buys", "buy", "bought", "combine", "combines", "combination",
    "explores", "exploring", "reportedly", "press", "release",
    "lead", "leads", "led", "support", "supports", "create", "creates",
    "debt", "financing", "facility", "billion", "million", "trillion",
    "advises", "growth", "drive", "stock", "surges", "market", "bets",
    "scaled", "up", "ceo", "thinks", "focused", "flavor", "mulls",
    "gets", "candid", "transparent", "conversation", "around", "after", "reviving",
    "remaining", "nsrgf", "labs", "sparks", "historic",
    "weighs", "valuation", "signals", "potential", "upside",
    "outlook", "insights", "navigator", "finance",
    "wire", "business", "news", "statistics", "nyse", "nasdaq",
}

# Deal-type detection, first match wins in this priority order.
_DEAL_TYPE_PRIORITY: list[tuple[str, list[str]]] = [
    ("merger", ["merger", "merges"]),
    ("acquisition", ["acquisition", "acquires", "acquired", "buy", "buys",
                     "bought", "buyout", "takeover", "deal to buy"]),
    ("joint venture", ["joint venture"]),
    ("divestment", ["divestment", "divests", "divestiture", "sale"]),
    ("stake", ["stake sale", "stake"]),
    ("funding", ["funding", "raises"]),
    ("investment", ["investment"]),
]

# Country/region surface forms. Acronyms are matched case-sensitively (a lowercase
# "us" is a pronoun, not a country); full names case-insensitively.
_GEO_ACRONYMS = {"US": "US", "U.S.": "US", "USA": "US", "UK": "UK", "U.K.": "UK", "EU": "Europe"}
_GEO_NAMES = {
    "india": "India", "united states": "US", "america": "US", "britain": "UK",
    "united kingdom": "UK", "europe": "Europe", "china": "China", "japan": "Japan",
    "brazil": "Brazil", "germany": "Germany", "france": "France", "australia": "Australia",
    "canada": "Canada", "mexico": "Mexico", "indonesia": "Indonesia", "vietnam": "Vietnam",
    "singapore": "Singapore", "south korea": "South Korea", "middle east": "Middle East",
    "africa": "Africa", "latin america": "Latin America", "southeast asia": "Southeast Asia",
    "global": "Global",
}

# CATEGORY_TERMS -> display bucket (first matching term across cluster text wins).
_CATEGORY_BUCKETS = {
    "food": "Food & Beverage", "beverage": "Food & Beverage",
    "frozen foods": "Food & Beverage", "nutrition": "Food & Beverage",
    "dairy": "Dairy",
    "snacks": "Snacks & Packaged Foods", "confectionery": "Snacks & Packaged Foods",
    "packaged foods": "Snacks & Packaged Foods",
    "beauty": "Beauty & Personal Care", "cosmetics": "Beauty & Personal Care",
    "skincare": "Beauty & Personal Care", "personal care": "Beauty & Personal Care",
    "home care": "Household & Home Care", "household products": "Household & Home Care",
    "hygiene": "Household & Home Care", "cleaning products": "Household & Home Care",
    "pet food": "Other", "baby care": "Other",
}

# Verbatim deal-value extraction (display string, no parsing/normalization —
# no-hallucination policy: report exactly what the text says or "undisclosed").
_VALUE_RE = re.compile(
    r"[$€£₹]\s?\d[\d,.]*\s?(?:million|billion|trillion|crore|lakh|mn|bn|[MBmb]\b)?"
    r"|\b\d[\d,.]*\s?(?:million|billion|crore|mn|bn)\b"
    r"|\b(?:USD|INR|EUR)\s?\d[\d,.]*",
    re.IGNORECASE,
)

_SOURCE_SUFFIX_RE = re.compile(r"\s+(?:-|\|)\s+.{1,45}$")


def _strip_accents(text: str) -> str:
    """ASCII-fold accented characters (Nestlé -> Nestle) for matching."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _norm_company(name: str) -> str:
    """Normalize a company name for set comparison (case- and accent-insensitive)."""
    return _strip_accents(name).lower().strip()


def _display_title(title: str) -> str:
    """Remove a short publisher suffix before display-entity extraction."""
    return _SOURCE_SUFFIX_RE.sub("", title or "").strip()


_WATCHLIST_NORM = {}  # normalized form -> canonical display form (first wins)
for _c in COMPANY_WATCHLIST:
    _WATCHLIST_NORM.setdefault(_norm_company(_c), _c)
_WATCHLIST_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(_strip_accents(c)) for c in COMPANY_WATCHLIST) + r")\b",
    re.IGNORECASE,
)


def extract_companies(title: str, snippet: str = "") -> list[str]:
    """Extract candidate company names from an article.

    Watchlist matches (case/accent-insensitive, over title+snippet) plus
    capitalized 1-3 token phrases from the raw title, cut at the first generic
    stoplisted token. Returns display forms, de-duplicated, order preserved.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        key = _norm_company(name)
        if key and key not in seen:
            seen.add(key)
            found.append(name)

    for m in _WATCHLIST_RE.finditer(_strip_accents(f"{title} {snippet}")):
        add(_WATCHLIST_NORM.get(_norm_company(m.group()), m.group()))

    for m in _CAP_PHRASE_RE.finditer(_display_title(title)):
        tokens = m.group().split()
        kept = []
        for tok in tokens:
            if tok.lower().strip(".,'&-") in _PHRASE_STOPLIST:
                break
            kept.append(tok)
        if kept and not (len(kept) == 1 and len(kept[0]) < 3):
            add(" ".join(kept))

    return found


def _cluster_date_ok(df: pd.DataFrame, left: list, right: list) -> bool:
    """True when all dated rows in two clusters fit inside the merge window."""
    dates = pd.concat([df.loc[left, "published_at"], df.loc[right, "published_at"]]).dropna()
    if dates.empty:
        return False
    return dates.max() - dates.min() <= pd.Timedelta(days=CLUSTER_DATE_WINDOW_DAYS)


def _max_pair_similarity(sim, pos: dict, left: list, right: list) -> float:
    """Maximum TF-IDF similarity between members of two clusters."""
    if sim is None:
        return 0.0
    return max(float(sim[pos[a], pos[b]]) for a in left for b in right)


def _merge_related_clusters(
    clusters: list[dict],
    df: pd.DataFrame,
    comp_sets: dict,
    sim,
    pos: dict,
) -> list[dict]:
    """Conservative second pass: merge clusters sharing companies and enough text.

    The first pass is intentionally greedy. This pass catches same-event coverage
    that used different wording but shares an extracted company and has at least
    modest title/snippet similarity inside the date window.
    """
    merged = [{"seed": c["seed"], "members": list(c["members"])} for c in clusters]
    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            if changed:
                break
            left_companies = set().union(*(comp_sets[idx] for idx in merged[i]["members"]))
            for j in range(i + 1, len(merged)):
                right_companies = set().union(*(comp_sets[idx] for idx in merged[j]["members"]))
                if not (left_companies & right_companies):
                    continue
                if not _cluster_date_ok(df, merged[i]["members"], merged[j]["members"]):
                    continue
                if _max_pair_similarity(sim, pos, merged[i]["members"], merged[j]["members"]) < 0.25:
                    continue
                merged[i]["members"].extend(merged[j]["members"])
                del merged[j]
                changed = True
                break
    return merged


def _detect_deal_type(text_lower: str) -> str:
    """First deal type whose terms appear in the cluster text, by priority."""
    for deal_type, terms in _DEAL_TYPE_PRIORITY:
        if any(re.search(rf"\b{re.escape(t)}\b", text_lower) for t in terms):
            return deal_type
    return "unknown"


def _detect_geography(text: str) -> str:
    """First country/region mentioned in the text, else 'Not specified'.

    Note: the plan's GDELT sourcecountry fallback is not available — the Article
    schema does not retain sourcecountry — so text scan is the only signal.
    """
    for surface, display in _GEO_ACRONYMS.items():
        if re.search(rf"\b{re.escape(surface)}(?![\w.])", text):
            return display
    lower = text.lower()
    for surface, display in _GEO_NAMES.items():
        if re.search(rf"\b{re.escape(surface)}\b", lower):
            return display
    return "Not specified"


def _detect_category(text_lower: str) -> str:
    """Display bucket of the first CATEGORY_TERMS match in cluster text."""
    for term in CATEGORY_TERMS:
        if re.search(rf"\b{re.escape(term)}\b", text_lower):
            return _CATEGORY_BUCKETS.get(term, "Other")
    return "Other"


def _extract_deal_value(texts: list[str]) -> str:
    """First monetary value visible in the texts, verbatim, else 'undisclosed'."""
    for text in texts:
        m = _VALUE_RE.search(text or "")
        if m:
            return m.group().strip()
    return "undisclosed"


def _why_it_matters(deal_type: str, category: str, companies: list[str],
                    geography: str, source_count: int, deal_value: str) -> str:
    """Deterministic, evidence-bounded summary line (never generative)."""
    s = f"Signals {deal_type} activity in {category}"
    if companies:
        s += f", involving {', '.join(companies)}"
    if geography != "Not specified":
        s += f" in {geography}"
    s += f". {source_count} source(s) reporting"
    if deal_value != "undisclosed":
        s += f", value {deal_value}"
    return s + "."


def cluster_deals(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Cluster canonical include/watchlist articles into deal events.

    Greedy single pass: articles newest-first; an article joins the first cluster
    whose SEED article is within CLUSTER_DATE_WINDOW_DAYS and similar enough
    (cosine >= 0.6, or >= 0.4 with at least one shared extracted company);
    otherwise it seeds a new cluster. No post-merge pass (order-dependent by
    design — documented limitation).

    Returns (df copy with cluster_id filled on clustered rows, list of cluster
    record dicts in creation order).
    """
    df = df.copy()
    df["cluster_id"] = ""
    if df.empty:
        return df, []
    sel = df[(df["dedupe_status"] == "canonical")
             & (df["status"].isin(["include", "watchlist"]))]
    sel = sel.sort_values("published_at", ascending=False)
    if sel.empty:
        return df, []

    idx_list = sel.index.tolist()
    texts = [f"{df.loc[i, 'normalized_title']} {df.loc[i, 'snippet'] or ''}".strip()
             for i in idx_list]
    try:
        matrix = TfidfVectorizer(min_df=1, stop_words="english").fit_transform(texts)
        sim = cosine_similarity(matrix)
    except ValueError:  # empty vocabulary -> no text similarity available
        sim = None
    pos = {idx: p for p, idx in enumerate(idx_list)}
    companies_by_idx = {
        i: extract_companies(df.loc[i, "title"], df.loc[i, "snippet"] or "")
        for i in idx_list
    }
    comp_sets = {i: {_norm_company(c) for c in companies_by_idx[i]} for i in idx_list}

    clusters: list[dict] = []  # {"seed": idx, "members": [idx, ...]}
    for idx in idx_list:
        joined = False
        for cluster in clusters:
            seed = cluster["seed"]
            a, b = df.loc[seed, "published_at"], df.loc[idx, "published_at"]
            if pd.isna(a) or pd.isna(b) or abs(a - b) > pd.Timedelta(days=CLUSTER_DATE_WINDOW_DAYS):
                continue
            cos = sim[pos[seed], pos[idx]] if sim is not None else 0.0
            shared = bool(comp_sets[seed] & comp_sets[idx])
            if cos >= CLUSTER_COSINE_THRESHOLD or (shared and cos >= CLUSTER_COSINE_WITH_COMPANY):
                cluster["members"].append(idx)
                joined = True
                break
        if not joined:
            clusters.append({"seed": idx, "members": [idx]})
    clusters = _merge_related_clusters(clusters, df, comp_sets, sim, pos)

    records: list[dict] = []
    for n, cluster in enumerate(clusters, start=1):
        cluster_id = f"C{n:03d}"
        members = cluster["members"]
        df.loc[members, "cluster_id"] = cluster_id
        rows = df.loc[members]
        # best article: highest credibility, then relevance
        best_first = rows.sort_values(["credibility_score", "relevance_score"],
                                      ascending=False)
        best = best_first.iloc[0]
        member_texts = [f"{r['title']} {r['snippet'] or ''}" for _, r in best_first.iterrows()]
        all_text = " ".join(member_texts)
        all_text_lower = all_text.lower()

        # Union of member companies, best article first; a name that is a prefix
        # of an already-kept name (or vice versa) collapses to the longer form
        # ("Sample Snacks" + "Sample Snacks Co" -> "Sample Snacks Co").
        companies: list[str] = []
        for _, r in best_first.iterrows():
            for c in companies_by_idx[r.name]:
                k = _norm_company(c)
                absorbed = False
                for j, kept in enumerate(companies):
                    kk = _norm_company(kept)
                    if k.startswith(kk) or kk.startswith(k):
                        if len(k) > len(kk):
                            companies[j] = c
                        absorbed = True
                        break
                if not absorbed:
                    companies.append(c)
        companies = companies[:5]

        source_keys = [source_key(r) for _, r in rows.iterrows()]
        tier_labels = rows["credibility_tier"].tolist()
        n_sources = len({k for k in source_keys if k})
        deal_type = _detect_deal_type(all_text_lower)
        category = _detect_category(all_text_lower)
        geography = _detect_geography(all_text)
        deal_value = _extract_deal_value(member_texts)
        relevance = int(rows["relevance_score"].max())
        max_article_credibility = int(rows["credibility_score"].max())
        credibility = cluster_credibility(rows["credibility_score"].tolist(),
                                          source_keys, tier_labels)
        confidence = confidence_label(relevance, credibility, n_sources,
                                      "tier_1" in tier_labels)

        records.append({
            "cluster_id": cluster_id,
            "canonical_headline": best["title"],
            "deal_type": deal_type,
            "companies": companies if companies else "Not clearly identified",
            "geography": geography,
            "category": category,
            "deal_value": deal_value,
            "relevance_score": relevance,
            "credibility_score": credibility,
            "max_article_credibility": max_article_credibility,
            "confidence": confidence,
            "why_it_matters": _why_it_matters(deal_type, category, companies,
                                              geography, n_sources, deal_value),
            "sources": [
                {"title": r["title"], "url": r["url"], "domain": r["domain"],
                 "source_name": r["source_name"],
                 "published_at": r["published_at"].isoformat() if pd.notna(r["published_at"]) else "",
                 "credibility_tier": r["credibility_tier"]}
                for _, r in best_first.iterrows()
            ],
            "article_count": len(members),
            "source_count": n_sources,
            "earliest_published_at": rows["published_at"].min().isoformat() if rows["published_at"].notna().any() else "",
            "latest_published_at": rows["published_at"].max().isoformat() if rows["published_at"].notna().any() else "",
            "is_sample": bool(rows["is_sample"].all()),
        })

    return df, records
