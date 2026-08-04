"""modules.search.url_utils — URL normalization (Round 2 Phase 2).

R2-2.6: URL normalization strips only tracking parameters (utm_*, fbclid,
gclid) and preserves business parameters (id, page, query, version, ...).

Previous behavior stripped the entire query string, which broke pagination
and product URLs. This module implements the surgical stripping policy.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Tracking parameters to strip (prefix match for utm_*).
TRACKING_PREFIXES = ('utm_',)
TRACKING_EXACT = frozenset({
    'fbclid',       # Facebook Click ID
    'gclid',        # Google Click ID
    'dclid',        # DoubleClick ID
    'msclkid',      # Microsoft Clarity ID
    'mc_eid',       # Mailchimp Event ID
    'mc_cid',       # Mailchimp Campaign ID
    'yclid',        # Yahoo Click ID
    'twclid',       # Twitter Click ID
    'igshid',       # Instagram Share ID
    'ref',          # Generic referrer tracking
    'ref_src',      # Referrer source
    'ref_url',      # Referrer URL
    '_hsenc', '_hsmi',  # HubSpot tracking
    'vero_id',      # Vero tracking
    'campaign_id',  # Generic campaign tracking
})

# Business parameters that MUST be preserved.
PRESERVE_EXACT = frozenset({
    'id', 'page', 'query', 'version', 'v', 'lang', 'language',
    'q', 'search', 'keyword', 'keywords', 'sort', 'order',
    'filter', 'category', 'type', 'tab', 'section', 'p',
    'product_id', 'item_id', 'sku', 'asin', 'isbn',
    'doc', 'path', 'route', 'view', 'format',
})

# Parameter name pattern for utm_* (prefix).
_UTM_PATTERN = re.compile(r'^utm_\w+$', re.IGNORECASE)


def is_tracking_param(name: str) -> bool:
    """True if the parameter is a tracking parameter that should be stripped."""
    if not name:
        return False
    name_lower = name.lower()
    if name_lower in TRACKING_EXACT:
        return True
    for prefix in TRACKING_PREFIXES:
        if name_lower.startswith(prefix):
            return True
    if _UTM_PATTERN.match(name):
        return True
    return False


def is_business_param(name: str) -> bool:
    """True if the parameter is a business parameter that must be preserved."""
    return name.lower() in PRESERVE_EXACT


def normalize_url(url: str) -> str:
    """Normalize a URL by stripping only tracking parameters.

    Policy (R2-2.6):
        - Strip utm_*, fbclid, gclid, and other tracking params (see TRACKING_EXACT)
        - Preserve id, page, query, version, and other business params
        - Preserve path, fragment, and scheme
        - Sort remaining params for stable comparison
        - Empty query string → drop the '?'

    Args:
        url: input URL (may be empty).

    Returns:
        Normalized URL. Returns '' for empty input. Returns the original
        string on parse failure (defensive).
    """
    if not url or not isinstance(url, str):
        return ''
    url = url.strip()
    if not url:
        return ''
    try:
        parts = urlsplit(url)
    except ValueError:
        return url  # unparseable — return as-is

    # Filter query parameters
    if not parts.query:
        return urlunsplit(parts)

    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not is_tracking_param(k)]
    # Sort for stable comparison (alphabetical by key, then value)
    kept.sort()
    new_query = urlencode(kept, doseq=True)
    new_parts = parts._replace(query=new_query)
    return urlunsplit(new_parts)


def urls_equivalent(url_a: str, url_b: str) -> bool:
    """True if two URLs are equivalent after normalization."""
    return normalize_url(url_a) == normalize_url(url_b)


def deduplicate_urls(urls: list[str]) -> list[str]:
    """Return URLs with duplicates removed (after normalization).

    Preserves first-occurrence order.
    """
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        n = normalize_url(u)
        if n not in seen:
            seen.add(n)
            out.append(u)
    return out
