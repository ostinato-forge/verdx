"""Keyword matching — tokenization, Jaccard similarity, pattern search."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from verdx.engine.flywheel.types import Pattern

MERGE_THRESHOLD = 0.5
SEARCH_THRESHOLD = 0.2
DOMAIN_BOOST = 1.2

_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "may",
        "might",
        "must",
        "no",
        "not",
        "of",
        "on",
        "or",
        "shall",
        "should",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
        "would",
    ]
)


def tokenize(text: str) -> frozenset[str]:
    """Extract keyword tokens from text.

    Lowercases, extracts runs of [a-z0-9_] with 3+ chars, drops stopwords.
    """
    return frozenset(_TOKEN_RE.findall(text.lower())) - STOPWORDS


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity: |intersection| / |union|."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def find_best_matches(
    query_tokens: frozenset[str],
    patterns: list[Pattern],
    *,
    domain: str | None = None,
    threshold: float = SEARCH_THRESHOLD,
    top: int = 3,
) -> list[tuple[Pattern, float]]:
    """Return patterns above threshold, sorted by score desc then seen_count desc."""
    scored: list[tuple[Pattern, float]] = []
    for pattern in patterns:
        symptom_tokens = frozenset(pattern.symptoms)
        score = jaccard(query_tokens, symptom_tokens)
        if domain and domain in (pattern.domain, *pattern.targets):
            score *= DOMAIN_BOOST
        if score >= threshold:
            scored.append((pattern, score))
    scored.sort(key=lambda x: (-x[1], -x[0].seen_count))
    return scored[:top]
