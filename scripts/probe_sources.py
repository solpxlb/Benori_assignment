"""Phase 0 de-risk probe (throwaway): confirm live data sources return usable data.

Run: python scripts/probe_sources.py

Probes:
1. GDELT DOC 2.0 API (primary source)
2. Google News RSS (fallback source)
3. PR-wire RSS feeds (optional corroboration) — decision gate for Phase 2:
   only feeds confirmed alive here get a connector; dead feeds -> README mention only.

FINDINGS (recorded after live runs on 2026-06-10):

1. GDELT DOC 2.0:
   - QUERY SYNTAX TRAP: quoting a single short word (e.g. "FMCG", "food") makes the
     API return HTTP 200 + content-type text/html + body "The specified phrase is too
     short." Quote ONLY multi-word phrases; leave single words bare.
   - Without `sourcelang:english` the artlist is dominated by non-English articles
     (Chinese/Japanese/Vietnamese local news) even for English query terms. Append
     `sourcelang:english` to every query.
   - Working query shape (verified, 50 results):
     (acquisition OR merger OR "stake sale") (FMCG OR "consumer goods" OR food OR beverage) sourcelang:english
   - Precision is still low (earnings transcripts, farm-bill politics, auto M&A leak
     through) — that's what relevance scoring is for. Real deals do appear
     (e.g. "Tate & Lyle bought for $5B by Ingredion").
   - RATE LIMIT IS STRICT: 1 request per 5 seconds. Violations return HTTP 429,
     content-type None, plain-text body, and the penalty can persist for 60s+ after
     repeated hits. Sleep >= 5s between queries in Phase 2 (NOT the 0.5s originally
     planned). Also seen: SSL-handshake read timeouts under load — timeout=10 with
     try/except is mandatory.
   - Error pages come back with HTTP 200, so check content-type / wrap the JSON parse;
     status code alone is not a health signal.
   - Article keys in artlist mode: domain, language, seendate, socialimage,
     sourcecountry, title, url, url_mobile. NO snippet field. seendate format
     YYYYMMDDTHHMMSSZ (UTC).

2. Google News RSS: healthy. 100 entries for "FMCG acquisition", bozo=False,
   relevant titles, `source.title` present (real publisher name), `published` in
   RFC-822. Links are news.google.com redirects (real domain not recoverable).

3. PR-wire feeds (Phase 2 decision):
   - ALIVE  prnewswire-consumer (consumer-products-retail category, 20 entries)
   - ALIVE  globenewswire-ma (M&A subject code, 20 entries)
   - ALIVE  globenewswire-consumer (consumer-products industry, 20 entries)
   - ALIVE  prnewswire-all (firehose — too broad, skip in favor of the category feed)
   - DEAD   businesswire-all (HTTP 200 but 0 entries; feedburner-era URL defunct)
   => Phase 2 builds rss_feeds connector for: prnewswire-consumer, globenewswire-ma,
      globenewswire-consumer. Business Wire goes in README source-strategy only.
"""

import json
from urllib.parse import quote_plus

import feedparser
import requests

TIMEOUT = 10

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# Verified shape (see FINDINGS): quote only multi-word phrases, force English.
# The original probe used quoted single words ("FMCG", "food") which returns an
# HTML error page — kept here as a comment for the record:
#   '("acquisition" OR "merger" OR "stake") ("FMCG" OR "consumer goods" OR "food" OR "beverage")'
GDELT_QUERY = '(acquisition OR merger OR "stake sale") (FMCG OR "consumer goods" OR food OR beverage) sourcelang:english'

GOOGLE_NEWS_URL = (
    "https://news.google.com/rss/search?q="
    + quote_plus("FMCG acquisition")
    + "&hl=en-US&gl=US&ceid=US:en"
)

# Candidate PR-wire category feeds (these break and move often).
PR_WIRE_FEEDS = [
    ("prnewswire-all", "https://www.prnewswire.com/rss/news-releases-list.rss"),
    ("prnewswire-consumer", "https://www.prnewswire.com/rss/consumer-products-retail-latest-news/consumer-products-retail-latest-news-list.rss"),
    ("businesswire-all", "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEFpRWQ%3D%3D"),
    ("globenewswire-ma", "https://www.globenewswire.com/RssFeed/subjectcode/16-Mergers%20And%20Acquisitions/feedTitle/GlobeNewswire%20-%20Mergers%20and%20Acquisitions"),
    ("globenewswire-consumer", "https://www.globenewswire.com/RssFeed/industry/9576-Consumer%20Products/feedTitle/GlobeNewswire%20-%20Consumer%20Products"),
]


def probe_gdelt() -> None:
    print("=" * 60)
    print("1) GDELT DOC 2.0")
    params = {
        "query": GDELT_QUERY,
        "mode": "artlist",
        "format": "json",
        "maxrecords": 50,
        "timespan": "7d",
        "sort": "datedesc",
    }
    try:
        r = requests.get(GDELT_URL, params=params, timeout=TIMEOUT)
        print(f"   HTTP {r.status_code}, content-type: {r.headers.get('content-type')}")
        try:
            data = r.json()
        except json.JSONDecodeError:
            print(f"   NON-JSON response (first 200 chars): {r.text[:200]!r}")
            return
        articles = data.get("articles", [])
        print(f"   {len(articles)} articles")
        for a in articles[:5]:
            print(f"   - [{a.get('seendate')}] {a.get('domain')}: {a.get('title')}")
        if articles:
            print(f"   keys on first article: {sorted(articles[0].keys())}")
    except requests.RequestException as e:
        print(f"   FAILED: {e}")


def probe_google_news() -> None:
    print("=" * 60)
    print("2) Google News RSS")
    try:
        feed = feedparser.parse(GOOGLE_NEWS_URL)
        print(f"   {len(feed.entries)} entries (bozo={feed.bozo})")
        for e in feed.entries[:5]:
            src = e.get("source", {}).get("title", "?")
            print(f"   - [{e.get('published')}] {src}: {e.get('title')}")
    except Exception as e:  # feedparser rarely raises, but be safe
        print(f"   FAILED: {e}")


def probe_pr_wires() -> None:
    print("=" * 60)
    print("3) PR-wire RSS feeds (alive/dead decision for Phase 2)")
    for label, url in PR_WIRE_FEEDS:
        try:
            r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
            feed = feedparser.parse(r.content)
            n = len(feed.entries)
            verdict = "ALIVE" if n > 0 else "DEAD/EMPTY"
            print(f"   [{verdict}] {label}: HTTP {r.status_code}, {n} entries")
            if n:
                print(f"      first: {feed.entries[0].get('title')}")
        except requests.RequestException as e:
            print(f"   [DEAD] {label}: {e}")


if __name__ == "__main__":
    probe_gdelt()
    probe_google_news()
    probe_pr_wires()
