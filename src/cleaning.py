"""Cleaning: URL canonicalization, title normalization, domain extraction, snippets.

These feed the dedup passes (Phase 4): exact-URL dedup keys on canonical_url,
title dedup keys on normalized_title.
"""

import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import pandas as pd
import tldextract

# Params that vary per-share/per-campaign without changing the article identity.
TRACKING_PARAMS = {"fbclid", "gclid", "msclkid", "cmpid", "ref", "source", "output"}
TRACKING_PREFIXES = ("utm_",)

# Words that prefix/decorate headlines without carrying story identity.
TITLE_NOISE_WORDS = {"breaking", "update", "exclusive", "latest", "report", "says"}

# A title suffix after " - " / " | " shorter than this is treated as a source name.
MAX_SOURCE_SUFFIX_LEN = 35

SNIPPET_MAX_LEN = 500

_TAG_RE = re.compile(r"<[^>]+>")
_PUNCT_RE = re.compile(r"[^\w\s]")


def canonicalize_url(url: str) -> str:
    """Normalize a URL for exact-duplicate detection.

    Lowercases scheme+host, strips leading "www.", drops the fragment and any
    trailing slash, removes tracking params (utm_*, fbclid, ...) and sorts the
    remaining query params so param order never defeats the match.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parts.path.rstrip("/")
    params = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS
        and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    query = urlencode(sorted(params))
    return urlunsplit((parts.scheme.lower(), host, path, query, ""))


def normalize_title(title: str) -> str:
    """Normalize a headline for title-based duplicate detection.

    Lowercase; strip a short trailing " - Source" / " | Source" suffix (likely a
    publisher name, not story content); remove punctuation and headline noise
    words (breaking/update/...); collapse whitespace.
    """
    if not title:
        return ""
    t = title.lower()
    cut = max(t.rfind(" - "), t.rfind(" | "))
    if cut != -1 and len(t) - (cut + 3) < MAX_SOURCE_SUFFIX_LEN:
        t = t[:cut]
    t = _PUNCT_RE.sub(" ", t)
    words = [w for w in t.split() if w not in TITLE_NOISE_WORDS]
    return " ".join(words)


def extract_domain(url: str) -> str:
    """Return the registered domain (domain.suffix) of a URL via tldextract."""
    if not url:
        return ""
    ext = tldextract.extract(url)
    return f"{ext.domain}.{ext.suffix}" if ext.suffix else ext.domain


def clean_snippet(text: str) -> str:
    """Strip HTML tags, collapse whitespace, truncate to SNIPPET_MAX_LEN chars."""
    if not text or not isinstance(text, str):
        return ""
    t = _TAG_RE.sub(" ", text)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:SNIPPET_MAX_LEN]


def apply_cleaning(df: pd.DataFrame) -> pd.DataFrame:
    """Add canonical_url / normalized_title / domain, coerce dates, clean snippets.

    Returns a copy; the input frame is not mutated.
    """
    df = df.copy()
    if df.empty:
        return df
    df["canonical_url"] = df["url"].fillna("").map(canonicalize_url)
    df["normalized_title"] = df["title"].fillna("").map(normalize_title)
    df["domain"] = df.apply(
        lambda r: r["domain"] if r.get("domain") else extract_domain(r["url"]), axis=1
    )
    df["snippet"] = df["snippet"].fillna("").map(clean_snippet)
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True, errors="coerce")
    df["retrieved_at"] = pd.to_datetime(df["retrieved_at"], utc=True, errors="coerce")
    return df
