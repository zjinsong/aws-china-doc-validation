"""Search AWS China 'What's New' announcements at amazonaws.cn/new.

The What's New index page renders announcement cards client-side, but the
initial HTML still contains the announcement permalinks in the form
``/en/new/<year>/<slug>/``. Each slug is a hyphenated, human-readable summary
of the announcement (for example
``amazon-lambda-durable-functions-are-available``), which is enough to match a
service/feature against real China-region launch announcements without needing a
browser or a private JSON API.

An announcement that a feature "is available" / "now supports" X in the China
Regions is strong, authoritative evidence that the feature has actually landed
in aws-cn - stronger than the static botocore endpoint catalog, which lags
behind new launches.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import requests

_BASE = "https://www.amazonaws.cn"
_INDEX_PATHS = ("/en/new/", "/en/new/2026/", "/en/new/2025/")
_LINK_RE = re.compile(r"/en/new/(\d{4})/([a-z0-9\-]+)/")
# Words that, when present in a slug, signal the item is an availability launch.
_AVAILABILITY_HINTS = (
    "is-available",
    "are-available",
    "now-available",
    "available-in",
    "now-supports",
    "adds-support",
    "adds-",
    "launches",
    "launching",
    "announcing-",
    "expands",
    "now-generally-available",
    "in-china",
    "china-region",
)


def _slug_to_text(slug: str) -> str:
    return slug.replace("-", " ")


def fetch_announcement_slugs(http_get=requests.get, paths: tuple[str, ...] = _INDEX_PATHS) -> list[dict]:
    """Return de-duplicated announcement entries from the What's New index pages.

    Each entry is ``{"year", "slug", "text", "url"}``. Failures fetching a page
    are ignored so that a partial outage still yields whatever was reachable.
    """
    seen: set[tuple[str, str]] = set()
    entries: list[dict] = []
    for path in paths:
        try:
            response = http_get(
                urljoin(_BASE, path), timeout=20, headers={"User-Agent": "China-Doc-TruthKeeper/1.0"}
            )
            response.raise_for_status()
            html = response.text
        except requests.RequestException:
            continue
        for match in _LINK_RE.finditer(html):
            year, slug = match.group(1), match.group(2)
            key = (year, slug)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "year": year,
                    "slug": slug,
                    "text": _slug_to_text(slug),
                    "url": urljoin(_BASE, f"/en/new/{year}/{slug}/"),
                }
            )
    return entries


def _tokens(value: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", value.lower()) if t]


def search_announcements(
    service: str,
    feature: str,
    http_get=requests.get,
    limit: int = 10,
) -> dict:
    """Search China-region What's New announcements for a service/feature.

    Matching is token-based: an announcement matches when its slug contains the
    service token(s) and at least one feature token. Results are split into
    availability-signalling announcements and other related mentions.
    """
    entries = fetch_announcement_slugs(http_get=http_get)
    service_tokens = _tokens(service)
    feature_tokens = _tokens(feature)

    availability: list[dict] = []
    related: list[dict] = []
    for entry in entries:
        slug = entry["slug"].lower()
        slug_tokens = set(_tokens(slug))
        service_hit = any(tok in slug_tokens for tok in service_tokens) if service_tokens else False
        feature_hit = any(tok in slug_tokens for tok in feature_tokens) if feature_tokens else False
        if not (service_hit and feature_hit):
            continue
        is_availability = any(hint in slug for hint in _AVAILABILITY_HINTS)
        (availability if is_availability else related).append(entry)

    matched = (availability + related)[:limit]
    if availability:
        conclusion = "announced_available"
    elif related:
        conclusion = "related_mention"
    else:
        conclusion = "no_announcement_found"
    return {
        "source": "amazonaws.cn/new (AWS China What's New)",
        "conclusion": conclusion,
        "availability_announcements": availability[:limit],
        "related_announcements": related[:limit],
        "matched_count": len(matched),
        "scanned_announcements": len(entries),
    }
