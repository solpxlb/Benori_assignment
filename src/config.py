"""Central configuration: term lists, query families, thresholds, scoring constants.

Every weight/threshold lives here as a named constant with a one-line comment so it
can be inspected and defended. Query construction follows the Phase 0 probe findings:
quote ONLY multi-word phrases (quoting single short words makes GDELT return an
HTML error page), and append `sourcelang:english` to every GDELT query.
"""

# ---------------------------------------------------------------------------
# Term lists
# ---------------------------------------------------------------------------

FMCG_TERMS = ["FMCG", "CPG", "consumer packaged goods", "fast moving consumer goods",
              "consumer goods", "consumer brands", "packaged goods"]

CATEGORY_TERMS = ["food", "beverage", "dairy", "snacks", "confectionery", "packaged foods",
                  "frozen foods", "nutrition", "beauty", "cosmetics", "skincare",
                  "personal care", "home care", "household products", "hygiene",
                  "cleaning products", "pet food", "baby care"]

STRONG_DEAL_TERMS = ["acquisition", "acquires", "acquired", "merger", "merges",
                     "buyout", "takeover", "joint venture", "stake sale", "divestment"]

SOFT_DEAL_TERMS = ["investment", "strategic investment", "minority stake", "majority stake",
                   "stake", "funding", "raises", "private equity"]

COMPANY_WATCHLIST = ["Unilever", "Nestlé", "Nestle", "Procter & Gamble", "P&G", "PepsiCo",
                     "Coca-Cola", "Mondelez", "Mars", "Danone", "Kraft Heinz", "General Mills",
                     "Kellanova", "Colgate-Palmolive", "Reckitt", "L'Oréal", "L'Oreal",
                     "Kimberly-Clark", "Henkel", "Tata Consumer", "Hindustan Unilever",
                     "Dabur", "Marico", "Godrej Consumer", "ITC", "Britannia", "Emami"]

NEGATIVE_TERMS = ["earnings", "stock price", "share buyback", "dividend", "quarterly results",
                  "product launch", "marketing campaign", "crypto", "ETF", "real estate"]

# ---------------------------------------------------------------------------
# Network / fetch constants
# ---------------------------------------------------------------------------

GDELT_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"  # DOC 2.0 article search
GDELT_TIMEOUT = 10           # seconds per request; GDELT shows occasional SSL stalls
GDELT_SLEEP_BETWEEN = 10.0   # GDELT's documented limit is 1 req/5s but enforcement is erratic; 5-8s still trips 429s, 10s is reliable (Phase 2 finding)
MAX_RECORDS_PER_QUERY = 75   # GDELT maxrecords per query; 5 queries -> up to 375 raw rows

# PR-wire RSS feeds confirmed ALIVE by the Phase 0 probe (2026-06-10). Business Wire's
# feed was dead (HTTP 200, zero entries) and is deliberately absent — README mention only.
PR_WIRE_FEEDS: list[dict] = [
    {"url": "https://www.prnewswire.com/rss/consumer-products-retail-latest-news/consumer-products-retail-latest-news-list.rss",
     "source_label": "PR Newswire"},
    {"url": "https://www.globenewswire.com/RssFeed/subjectcode/16-Mergers%20And%20Acquisitions/feedTitle/GlobeNewswire%20-%20Mergers%20and%20Acquisitions",
     "source_label": "GlobeNewswire"},
    {"url": "https://www.globenewswire.com/RssFeed/industry/9576-Consumer%20Products/feedTitle/GlobeNewswire%20-%20Consumer%20Products",
     "source_label": "GlobeNewswire"},
]

# ---------------------------------------------------------------------------
# Dedup thresholds (used in Phase 4)
# ---------------------------------------------------------------------------

DEDUPE_FUZZY_THRESHOLD = 90        # rapidfuzz token_set_ratio >= this -> fuzzy-title duplicate
DEDUPE_TFIDF_THRESHOLD = 0.82      # TF-IDF cosine >= this -> near-duplicate (raise to 0.85 if false merges)
DEDUPE_DATE_WINDOW_DAYS = 14       # title/similarity passes only pair articles within this window

# ---------------------------------------------------------------------------
# Clustering thresholds (used in Phase 6)
# ---------------------------------------------------------------------------

CLUSTER_COSINE_THRESHOLD = 0.6         # join cluster on title+snippet cosine alone
CLUSTER_COSINE_WITH_COMPANY = 0.4      # lower cosine bar when >=1 extracted company is shared
CLUSTER_DATE_WINDOW_DAYS = 30          # articles beyond this gap never share a deal event

# ---------------------------------------------------------------------------
# Relevance classification cutoffs (used in Phase 5)
# ---------------------------------------------------------------------------

INCLUDE = 70    # relevance >= 70 -> status "include"
WATCHLIST = 55  # 55-69 -> "watchlist"; below -> "reject"


# ---------------------------------------------------------------------------
# Query families
# ---------------------------------------------------------------------------

def _gdelt_or_group(terms: list[str]) -> str:
    """Build a parenthesized GDELT OR group, quoting only multi-word phrases.

    Quoting a single short word makes GDELT return HTTP 200 + an HTML error page
    ("The specified phrase is too short.") — Phase 0 finding.
    """
    parts = [f'"{t}"' if " " in t else t for t in terms]
    return "(" + " OR ".join(parts) + ")"


def build_gdelt_query(deal_terms: list[str], topic_terms: list[str]) -> str:
    """Assemble a GDELT DOC 2.0 query: deal-term group AND topic group, English only."""
    return f"{_gdelt_or_group(deal_terms)} {_gdelt_or_group(topic_terms)} sourcelang:english"


# Compact term subsets for queries: GDELT ANDs the two groups, so the full lists
# would make queries needlessly long; recall is recovered across the 5 families.
_QUERY_DEAL_TERMS = ["acquisition", "acquires", "merger", "buyout", "takeover",
                     "stake sale", "joint venture", "divestment"]
_QUERY_FUNDING_TERMS = ["private equity", "funding round", "raises", "investment"]
# Top watchlist names, ASCII-safe for URL transport (accents/apostrophes excluded).
_QUERY_WATCHLIST = ["Unilever", "Nestle", "PepsiCo", "Coca-Cola", "Mondelez", "Danone",
                    "Kraft Heinz", "General Mills", "Kellanova", "Procter & Gamble"]

# Each family: GDELT-syntax string + plain-text variant for RSS search.
QUERY_FAMILIES: list[dict] = [
    {
        "name": "general_fmcg",
        "gdelt": build_gdelt_query(_QUERY_DEAL_TERMS,
                                   ["FMCG", "CPG", "consumer goods", "consumer brands",
                                    "packaged goods"]),
        "rss": "FMCG acquisition merger",
    },
    {
        "name": "food_beverage",
        "gdelt": build_gdelt_query(_QUERY_DEAL_TERMS,
                                   ["food", "beverage", "dairy", "snacks"]),
        "rss": "food beverage company acquisition",
    },
    {
        "name": "beauty_personal_care",
        "gdelt": build_gdelt_query(_QUERY_DEAL_TERMS,
                                   ["beauty", "cosmetics", "skincare", "personal care"]),
        "rss": "beauty personal care brand acquisition",
    },
    {
        "name": "pe_funding",
        "gdelt": build_gdelt_query(_QUERY_FUNDING_TERMS,
                                   ["consumer brand", "FMCG", "CPG", "consumer goods"]),
        "rss": "private equity consumer brand investment",
    },
    {
        "name": "watchlist",
        "gdelt": build_gdelt_query(_QUERY_WATCHLIST, _QUERY_DEAL_TERMS),
        "rss": "Unilever OR Nestle OR PepsiCo acquisition",
    },
]

GDELT_QUERIES: list[str] = [f["gdelt"] for f in QUERY_FAMILIES]
RSS_QUERIES: list[str] = [f["rss"] for f in QUERY_FAMILIES]
