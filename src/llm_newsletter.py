"""Optional OpenRouter newsletter polish layer.

The LLM never fetches data and never creates deal facts. It only rewrites the
already-derived cluster records into more readable prose. If the model is absent,
fails, returns malformed JSON, or introduces unsupported values, callers keep the
deterministic newsletter.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import requests


logger = logging.getLogger(__name__)

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "google/gemini-3.5-flash"
OPENROUTER_TIMEOUT = 10
OPENROUTER_MAX_TOKENS = 1400
OPENROUTER_TEMPERATURE = 0.2
OPENROUTER_SEED = 1711
MAX_SUMMARY_WORDS = 45
MAX_HEADLINE_WORDS = 18
MAX_TAKEAWAY_WORDS = 34
MIN_SIGNIFICANT_TOKEN_LENGTH = 4

# Safe editorial words the model may use without introducing new deal facts.
SAFE_EDITORIAL_TOKENS = {
    "activity", "adds", "across", "appears", "based", "business", "brief",
    "category", "cluster", "clusters", "company", "companies", "confidence",
    "concentrated", "deal", "deals", "event", "events", "evidence", "facts",
    "fmcg", "focus", "group", "high", "highlights", "included", "item",
    "items", "led", "low", "market", "medium", "news", "not", "provided",
    "public", "reader", "remains", "sector", "signal", "signals", "source",
    "sources", "summary", "this", "transaction", "transactions", "undisclosed",
    "watch", "watchlist", "with", "wording", "worth",
}

DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:,\s*\d{4})?"
    r"|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"
    r"|\b20\d{2}\b",
    re.IGNORECASE,
)
PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s?%")
URL_RE = re.compile(r"https?://|www\.|[\w.-]+@[\w.-]+\.[A-Za-z]{2,}", re.IGNORECASE)
PLAIN_NUMBER_RE = re.compile(r"(?<![A-Za-z$€£₹.-])\d+(?:\.\d+)?(?![%A-Za-z.-])")

SYSTEM_PROMPT = """You are the newsletter editor for DealLens FMCG.

Your task is to rewrite provided deterministic deal-cluster data into a concise FMCG M&A and investment newsletter for a business reader.

Rules:
1. Use only the facts in INPUT_JSON.
2. Do not invent, infer, estimate, or add company names, deal values, dates, geographies, sources, scores, or implications.
3. Preserve every cluster_id exactly.
4. Preserve deal values exactly as provided. If a value is "undisclosed", write "undisclosed".
5. Preserve geographies exactly as provided. If geography is "Not specified", write "not specified".
6. Do not create source links or citations. Source links are rendered separately by the app.
7. Do not provide investment advice, predictions, or speculation.
8. Do not mention scores, URLs, publisher names, or source names.
9. If a claim is not directly supported by INPUT_JSON, omit it.
10. Write in plain consulting-style business language. Be concise, specific, and easy to skim.
11. Do not write standalone numeric claims. Counts and scores are rendered separately by the app.
12. Return only JSON matching the provided schema."""


NEWSLETTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["executive_summary", "clusters"],
    "properties": {
        "executive_summary": {"type": "string"},
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["cluster_id", "headline", "takeaway"],
                "properties": {
                    "cluster_id": {"type": "string"},
                    "headline": {"type": "string"},
                    "takeaway": {"type": "string"},
                },
            },
        },
    },
}


@dataclass
class PolishResult:
    """Result of an optional AI-polish attempt."""

    newsletter_md: str
    newsletter_data: dict
    used_ai: bool
    status: str
    warning: str = ""


_POLISH_CACHE: dict[tuple[str, str], dict] = {}
_POLISH_FAILURE_CACHE: dict[tuple[str, str], str] = {}


def _word_count(text: str) -> int:
    """Count natural-language words for compactness validation."""
    return len(re.findall(r"\b[\w$€£₹.-]+\b", text or ""))


def _cluster_input(cluster: dict) -> dict:
    """Small evidence-bound cluster payload for the model."""
    companies = cluster.get("companies")
    if isinstance(companies, list):
        companies_text = ", ".join(str(c) for c in companies if c)
    else:
        companies_text = str(companies or "Not clearly identified")
    return {
        "cluster_id": cluster.get("cluster_id", ""),
        "headline": cluster.get("canonical_headline", ""),
        "deal_type": cluster.get("deal_type", "unknown"),
        "companies": companies_text,
        "category": cluster.get("category", "Other"),
        "geography": cluster.get("geography", "Not specified"),
        "deal_value": cluster.get("deal_value", "undisclosed"),
        "confidence": cluster.get("confidence", "Low"),
        "relevance_score": int(cluster.get("relevance_score") or 0),
        "credibility_score": int(cluster.get("credibility_score") or 0),
        "source_count": int(cluster.get("source_count") or 0),
        "why_it_matters": cluster.get("why_it_matters", ""),
    }


def _build_input(newsletter_data: dict) -> dict:
    """Build the JSON payload that constrains model-visible facts."""
    clusters = list(newsletter_data.get("highlights", [])) + list(newsletter_data.get("watchlist", []))
    return {
        "title": newsletter_data.get("title", "FMCG DealBrief"),
        "date_range": newsletter_data.get("date_range", "Selected range"),
        "data_mode": newsletter_data.get("data_mode", "LIVE"),
        "snapshot": newsletter_data.get("snapshot", {}),
        "clusters": [_cluster_input(c) for c in clusters],
    }


def _request_payload(newsletter_data: dict) -> dict:
    """OpenRouter chat-completions payload using strict structured output."""
    model_input = _build_input(newsletter_data)
    return {
        "model": OPENROUTER_MODEL,
        "temperature": OPENROUTER_TEMPERATURE,
        "max_tokens": OPENROUTER_MAX_TOKENS,
        "seed": OPENROUTER_SEED,
        "provider": {"require_parameters": True},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "dealbrief_polish",
                "strict": True,
                "schema": NEWSLETTER_SCHEMA,
            },
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "INPUT_JSON:\n" + json.dumps(model_input, ensure_ascii=False)},
        ],
    }


def _extract_text(response_json: dict) -> str:
    """Extract assistant content from an OpenRouter chat-completion response."""
    choices = response_json.get("choices") or []
    if not choices:
        raise ValueError("OpenRouter returned no choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text", "") for p in content if isinstance(p, dict)]
        return "".join(parts)
    raise ValueError("OpenRouter response had no text content")


def _parse_json(text: str) -> dict:
    """Parse model JSON, with a narrow fallback for fenced JSON blocks."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise ValueError("AI output was not a JSON object")
    return value


def _known_values(input_clusters: dict[str, dict]) -> set[str]:
    """Values that may appear verbatim in polished prose."""
    values: set[str] = set()
    for cluster in input_clusters.values():
        for key in [
            "cluster_id", "headline", "deal_value", "geography", "category",
            "deal_type", "companies", "confidence", "why_it_matters",
        ]:
            value = str(cluster.get(key) or "").strip()
            if value and value.lower() not in {"not specified", "undisclosed", "not clearly identified"}:
                values.add(value)
    return values


def _token_forms(token: str) -> set[str]:
    """Return simple singular/plural variants for lexical grounding."""
    token = token.lower().strip("._-")
    forms = {token}
    if token.endswith("ies") and len(token) > 4:
        forms.add(token[:-3] + "y")
    if token.endswith("es") and len(token) > 4:
        forms.add(token[:-2])
    if token.endswith("s") and len(token) > 4:
        forms.add(token[:-1])
    return {form for form in forms if len(form) >= MIN_SIGNIFICANT_TOKEN_LENGTH}


def _tokens_from_text(text: str) -> set[str]:
    """Extract normalized significant tokens from source or model text."""
    tokens: set[str] = set()
    for raw in re.findall(r"[A-Za-z][A-Za-z0-9&.-]*", text or ""):
        tokens.update(_token_forms(raw))
    return tokens


def _allowed_tokens(*values: Any) -> set[str]:
    """Build the set of fact-grounded tokens plus safe editorial vocabulary."""
    tokens = set(SAFE_EDITORIAL_TOKENS)
    for value in values:
        if isinstance(value, dict):
            tokens.update(_allowed_tokens(*value.values()))
        elif isinstance(value, (list, tuple, set)):
            tokens.update(_allowed_tokens(*value))
        else:
            tokens.update(_tokens_from_text(str(value or "")))
    return tokens


def _unsupported_token(text: str, allowed_tokens: set[str]) -> str:
    """Return the first significant token that is not fact-grounded or editorial."""
    for token in sorted(_tokens_from_text(text)):
        if token not in allowed_tokens:
            return token
    return ""


def _contains_unsupported_money(text: str, allowed_values: set[str]) -> bool:
    """Reject money-like values not present in deterministic cluster facts."""
    money_re = re.compile(
        r"[$€£₹]\s?\d[\d,.]*(?:\s?(?:million|billion|crore|mn|bn|m|b))?"
        r"|\b\d[\d,.]*\s?(?:million|billion|crore|mn|bn)\b"
        r"|\b(?:USD|INR|EUR)\s?\d[\d,.]*",
        re.IGNORECASE,
    )
    allowed_joined = " ".join(allowed_values).lower()
    for match in money_re.finditer(text or ""):
        if match.group().strip().lower() not in allowed_joined:
            return True
    return False


def _contains_unsupported_dates_or_percentages(text: str, allowed_values: set[str]) -> bool:
    """Reject date or percentage claims not present in deterministic cluster facts."""
    allowed_joined = " ".join(allowed_values).lower()
    for pattern in (DATE_RE, PERCENT_RE):
        for match in pattern.finditer(text or ""):
            if match.group().strip().lower() not in allowed_joined:
                return True
    return False


def _contains_plain_number(text: str) -> bool:
    """Reject standalone numeric claims; deterministic rendering owns counts and scores."""
    return bool(PLAIN_NUMBER_RE.search(text or ""))


def _contains_disallowed_link(text: str) -> bool:
    """Reject model-created links, domains, or email addresses."""
    return bool(URL_RE.search(text or ""))


def _contains_disallowed_dash(text: str) -> bool:
    """Reject em/en dashes so AI prose matches the app's copy discipline."""
    return "—" in (text or "") or "–" in (text or "")


def validate_polish(polished: dict, newsletter_data: dict) -> tuple[bool, str]:
    """Validate AI output against known cluster IDs, lengths, and visible values."""
    if not isinstance(polished.get("executive_summary"), str):
        return False, "missing executive_summary"
    executive_summary = polished["executive_summary"].strip()
    if _word_count(executive_summary) > MAX_SUMMARY_WORDS:
        return False, "executive_summary too long"
    clusters = polished.get("clusters")
    if not isinstance(clusters, list):
        return False, "clusters must be a list"

    input_clusters = {
        c.get("cluster_id", ""): _cluster_input(c)
        for c in list(newsletter_data.get("highlights", [])) + list(newsletter_data.get("watchlist", []))
    }
    input_clusters.pop("", None)
    expected_ids = set(input_clusters)
    seen_ids: set[str] = set()
    allowed_values = _known_values(input_clusters)
    global_allowed_tokens = _allowed_tokens(_build_input(newsletter_data))
    if _contains_disallowed_link(executive_summary):
        return False, "executive_summary contained link-like text"
    if _contains_disallowed_dash(executive_summary):
        return False, "executive_summary contained disallowed dash"
    if _contains_unsupported_money(executive_summary, allowed_values):
        return False, "unsupported money value in executive_summary"
    if _contains_unsupported_dates_or_percentages(executive_summary, allowed_values):
        return False, "unsupported date or percentage in executive_summary"
    if _contains_plain_number(executive_summary):
        return False, "unsupported number in executive_summary"
    unsupported_summary_token = _unsupported_token(executive_summary, global_allowed_tokens)
    if unsupported_summary_token:
        return False, f"unsupported term in executive_summary: {unsupported_summary_token}"

    for item in clusters:
        if not isinstance(item, dict):
            return False, "cluster item was not an object"
        cluster_id = str(item.get("cluster_id", "")).strip()
        if cluster_id not in expected_ids:
            return False, f"unknown cluster_id {cluster_id}"
        if cluster_id in seen_ids:
            return False, f"duplicate cluster_id {cluster_id}"
        seen_ids.add(cluster_id)
        headline = str(item.get("headline", "")).strip()
        takeaway = str(item.get("takeaway", "")).strip()
        if not headline or not takeaway:
            return False, f"empty headline/takeaway for {cluster_id}"
        if _word_count(headline) > MAX_HEADLINE_WORDS:
            return False, f"headline too long for {cluster_id}"
        if _word_count(takeaway) > MAX_TAKEAWAY_WORDS:
            return False, f"takeaway too long for {cluster_id}"
        combined = f"{headline} {takeaway}"
        if _contains_disallowed_link(combined):
            return False, f"link-like text for {cluster_id}"
        if _contains_disallowed_dash(combined):
            return False, f"disallowed dash for {cluster_id}"
        if _contains_unsupported_money(combined, allowed_values):
            return False, f"unsupported money value for {cluster_id}"
        if _contains_unsupported_dates_or_percentages(combined, allowed_values):
            return False, f"unsupported date or percentage for {cluster_id}"
        if _contains_plain_number(combined):
            return False, f"unsupported number for {cluster_id}"
        cluster_allowed_tokens = _allowed_tokens(input_clusters[cluster_id])
        unsupported_cluster_token = _unsupported_token(combined, cluster_allowed_tokens)
        if unsupported_cluster_token:
            return False, f"unsupported term for {cluster_id}: {unsupported_cluster_token}"

    missing = expected_ids - seen_ids
    if missing:
        return False, "missing cluster_id " + ", ".join(sorted(missing))
    return True, ""


def apply_polish(polished: dict, newsletter_data: dict) -> dict:
    """Return a deep-copied newsletter dict with validated AI prose attached."""
    out = copy.deepcopy(newsletter_data)
    out["ai_polished"] = True
    out["ai_model"] = OPENROUTER_MODEL
    out["ai_executive_summary"] = polished["executive_summary"].strip()
    by_id = {
        item["cluster_id"]: {
            "ai_headline": item["headline"].strip(),
            "ai_takeaway": item["takeaway"].strip(),
        }
        for item in polished["clusters"]
    }
    for section in ("highlights", "watchlist"):
        for cluster in out.get(section, []):
            cluster.update(by_id.get(cluster.get("cluster_id", ""), {}))
    return out


def _polished_result(polished: dict, newsletter_data: dict) -> PolishResult:
    """Render validated polish against the current newsletter run data."""
    polished_data = apply_polish(polished, newsletter_data)
    from src.newsletter import render_newsletter_markdown

    return PolishResult(
        render_newsletter_markdown(polished_data),
        polished_data,
        True,
        "used",
        "",
    )


def _cache_key(newsletter_data: dict, api_key: str) -> tuple[str, str]:
    """Stable cache key that fingerprints model-visible input and key without storing the key."""
    input_text = json.dumps(_build_input(newsletter_data), sort_keys=True, ensure_ascii=False)
    payload_hash = hashlib.sha256(input_text.encode("utf-8")).hexdigest()
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return payload_hash, key_hash


def clear_polish_cache() -> None:
    """Clear in-memory LLM polish cache. Used by tests."""
    _POLISH_CACHE.clear()
    _POLISH_FAILURE_CACHE.clear()


def polish_newsletter(
    newsletter_data: dict,
    deterministic_markdown: str,
    api_key: str | None,
    referer: str = "https://github.com/solpxlb/Benori_assignment",
    app_title: str = "DealLens FMCG",
) -> PolishResult:
    """Attempt an OpenRouter polish pass; return deterministic content on failure."""
    if not api_key:
        data = copy.deepcopy(newsletter_data)
        data["ai_polished"] = False
        return PolishResult(deterministic_markdown, data, False, "disabled", "OPENROUTER_API_KEY not configured")

    cache_key = _cache_key(newsletter_data, api_key)
    if cache_key in _POLISH_CACHE:
        return _polished_result(copy.deepcopy(_POLISH_CACHE[cache_key]), newsletter_data)
    if cache_key in _POLISH_FAILURE_CACHE:
        data = copy.deepcopy(newsletter_data)
        data["ai_polished"] = False
        return PolishResult(deterministic_markdown, data, False, "fallback", _POLISH_FAILURE_CACHE[cache_key])

    try:
        response = requests.post(
            OPENROUTER_ENDPOINT,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": referer,
                "X-OpenRouter-Title": app_title,
            },
            json=_request_payload(newsletter_data),
            timeout=OPENROUTER_TIMEOUT,
        )
        response.raise_for_status()
        polished = _parse_json(_extract_text(response.json()))
        ok, reason = validate_polish(polished, newsletter_data)
        if not ok:
            raise ValueError(reason)
        result = _polished_result(polished, newsletter_data)
        _POLISH_CACHE[cache_key] = copy.deepcopy(polished)
        return result
    except Exception as exc:
        logger.warning("AI newsletter polish failed: %s", exc)
        _POLISH_FAILURE_CACHE[cache_key] = str(exc)
        data = copy.deepcopy(newsletter_data)
        data["ai_polished"] = False
        return PolishResult(deterministic_markdown, data, False, "fallback", str(exc))
