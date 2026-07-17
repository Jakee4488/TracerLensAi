"""Deterministic query-complexity scoring for dynamic causal budgets.

Pure and ADK-free (unit-tests hermetically, like runtime.py). Zero LLM calls:
the whole point is to size the reasoning budget *before* spending any tokens,
so simple queries finish in fewer executor round-trips and complex ones get
room up to the loop ceiling.

The score maps a raw text signal to a tier, and each tier to a speed-biased
``{"max_steps", "max_replans"}`` budget. The router seeds these into session
state per causal turn (clamped by the CAUSAL_MAX_STEPS/REPLANS env ceilings).
"""

from __future__ import annotations

import re

# Speed-biased tiers: simple queries run shallow and fast; only genuinely
# complex ones approach the LOOP_MAX_ITERATIONS=16 structural ceiling.
TIER_BUDGETS = {
    "simple": {"max_steps": 3, "max_replans": 1},
    "moderate": {"max_steps": 5, "max_replans": 1},
    "complex": {"max_steps": 8, "max_replans": 2},
    "very_complex": {"max_steps": 12, "max_replans": 2},
}

# Score thresholds → tier. A score >= the value picks that tier (checked high
# to low). Tuned so a bare question lands in "simple" and a long multi-clause
# causal+comparison prompt lands in "very_complex".
_TIER_THRESHOLDS = (
    (7, "very_complex"),
    (4, "complex"),
    (2, "moderate"),
    (0, "simple"),
)

_CAUSAL_RE = re.compile(
    r"\b(cause[sd]?|caus\w+|effect|impact|why|driver|factor|because|"
    r"leads?\s+to|led\s+to|result|consequence|influence|contribut\w+)\b",
    re.IGNORECASE,
)
_COMPARISON_RE = re.compile(
    r"\b(compare|comparison|versus|vs|trade-?offs?|scenario|what\s+if|"
    r"if\b.*\bthen|difference between|relationship between)\b",
    re.IGNORECASE,
)
_CONJUNCTION_RE = re.compile(r"\b(and|or|but|while|whereas|as well as)\b", re.IGNORECASE)

# The proxy prepends attached-file contents as "--- Attached file: NAME ---
# ... --- End of file: NAME ---" blocks (see proxy/main.py _attachment_context).
# Complexity must reflect the *question*, not the size of pasted-in data — a
# large file would otherwise inflate the score and slow the run, the opposite
# of the intent. Strip those blocks before scoring.
_ATTACHMENT_BLOCK_RE = re.compile(
    r"---\s*Attached file:.*?---\s*End of file:.*?---", re.IGNORECASE | re.DOTALL
)


def score_query_complexity(query: str) -> int:
    """Raw complexity signal (>= 0) from cheap lexical features of the query."""
    text = _ATTACHMENT_BLOCK_RE.sub(" ", query or "").strip()
    if not text:
        return 0

    words = re.findall(r"\w+", text)
    n_words = len(words)

    score = 0

    # Length buckets — longer asks tend to carry more moving parts.
    if n_words >= 60:
        score += 3
    elif n_words >= 30:
        score += 2
    elif n_words >= 12:
        score += 1

    # Causal/analytical vocabulary — the pipeline's bread and butter.
    if _CAUSAL_RE.search(text):
        score += 2

    # Comparison / branching / scenario framing — multi-branch reasoning.
    if _COMPARISON_RE.search(text):
        score += 2

    # Multiple asks: extra question marks beyond the first.
    score += max(0, text.count("?") - 1)

    # Enumeration / conjunction density — more clauses, more components.
    conjunctions = len(_CONJUNCTION_RE.findall(text))
    separators = text.count(",") + text.count(";")
    score += min(3, (conjunctions + separators) // 2)

    return score


def tier_for_query(query: str) -> str:
    """Complexity tier name for a query."""
    score = score_query_complexity(query)
    for threshold, tier in _TIER_THRESHOLDS:
        if score >= threshold:
            return tier
    return "simple"


def budgets_for_query(query: str) -> dict:
    """Speed-biased ``{"max_steps", "max_replans"}`` for a query's complexity."""
    return dict(TIER_BUDGETS[tier_for_query(query)])
