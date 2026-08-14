"""Deterministic numeric extraction and tolerance comparison.

Pure: no ADK, no LLM, no I/O. Lives in ``src`` rather than ``tests/eval``
because *both* sides need it — the step controller extracts numbers from an
executor's ``OBSERVED:`` trailer to populate a node trace, and the evaluation
layer extracts numbers from the final answer to check them against ground
truth. Two copies of a number parser would drift, and a drifted parser makes
the agent and its grader disagree about what the agent actually said.

Tolerance is a **union**: a value passes if it is within the absolute
tolerance OR within the relative tolerance. Union rather than intersection
because relative tolerance is useless near zero (a true effect of 0.0 makes
every relative band zero-width) and absolute tolerance is useless for large
magnitudes. Callers set whichever bound is meaningful and may set both.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

# Matches: 1,234.5 | -2.0 | +3 | .75 | 2.5e-3 | 1e6
# Ordered so the thousands-separated form wins before the plain form.
_NUMBER_RE = re.compile(
    r"(?<![\w.])"                                   # not mid-identifier / mid-decimal
    r"([-+]?(?:"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"                 # 1,234 / 1,234.56
    r"|\d+\.\d+(?:[eE][-+]?\d+)?"                   # 2.0 / 2.5e-3
    r"|\.\d+(?:[eE][-+]?\d+)?"                      # .75
    r"|\d+(?:[eE][-+]?\d+)?"                        # 3 / 1e6
    r"))"
    r"(?![\w])"                                     # not followed by a word char
)

# The proxy injects attached files as delimited blocks; their contents are the
# *input* data, not anything the agent computed. Numbers extracted from them
# would let a check pass on an echo of its own fixture.
_ATTACHMENT_BLOCK_RE = re.compile(
    r"---\s*Attached file:.*?---\s*End of file:.*?---", re.IGNORECASE | re.DOTALL
)

# Fenced code blocks are likewise input/plumbing, not prose claims.
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


@dataclass(frozen=True)
class ParsedNumber:
    """One numeric literal found in text, with enough context to label it."""
    value: float
    start: int
    end: int
    raw: str
    is_percent: bool = False

    @property
    def as_fraction(self) -> float:
        """Percent literals expressed as a fraction (15% -> 0.15)."""
        return self.value / 100.0 if self.is_percent else self.value


def strip_non_prose(text: str) -> str:
    """Remove attached-file blocks and fenced code so only the agent's own
    prose claims remain. Replaces with spaces to keep offsets meaningful."""
    def _blank(match: re.Match) -> str:
        return " " * (match.end() - match.start())

    cleaned = _ATTACHMENT_BLOCK_RE.sub(_blank, text or "")
    return _FENCE_RE.sub(_blank, cleaned)


def extract_numbers(text: str, *, prose_only: bool = True) -> list[ParsedNumber]:
    """Every numeric literal in ``text``, in order of appearance."""
    source = strip_non_prose(text) if prose_only else (text or "")
    found: list[ParsedNumber] = []
    for match in _NUMBER_RE.finditer(source):
        raw = match.group(1)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:  # pragma: no cover - regex guarantees parseability
            continue
        if not math.isfinite(value):
            continue
        tail = source[match.end():match.end() + 1]
        found.append(ParsedNumber(
            value=value,
            start=match.start(1),
            end=match.end(1),
            raw=raw,
            is_percent=(tail == "%"),
        ))
    return found


def within_tolerance(actual: float, expected: float,
                     rel: Optional[float] = None,
                     abs_: Optional[float] = None) -> bool:
    """True when ``actual`` matches ``expected`` within either tolerance.

    With neither bound given this is exact equality, which is almost never what
    an LLM-produced number warrants — callers should set at least one.
    """
    if not (math.isfinite(actual) and math.isfinite(expected)):
        return False
    delta = abs(actual - expected)
    if abs_ is not None and delta <= abs_:
        return True
    if rel is not None and delta <= rel * abs(expected):
        return True
    return rel is None and abs_ is None and delta == 0.0


@dataclass(frozen=True)
class NumericMatch:
    """The closest numeric literal to a target, and whether it passed."""
    found: Optional[float]
    expected: float
    passed: bool
    error: Optional[float]
    raw: str = ""
    used_abs: bool = False


def find_match(text: str, expected: float, *,
               rel: Optional[float] = None,
               abs_: Optional[float] = None,
               match_abs: bool = False,
               prose_only: bool = True) -> NumericMatch:
    """Does any number in ``text`` match ``expected`` within tolerance?

    Scans every literal rather than guessing which one is "the" answer: a
    positional rule ("first number", "last number") breaks the moment the model
    reorders its prose, and this check exists to be robust to phrasing while
    staying strict about the value.

    ``match_abs`` also accepts the magnitude, for answers that carry direction
    in words instead of a sign ("reduces demand by 1.5" for a true -1.5).
    Whoever sets it is asserting the direction is checked some other way.

    Reports the *closest* candidate when nothing passes, so a failure says what
    the agent actually claimed instead of only that it was wrong.
    """
    candidates = extract_numbers(text, prose_only=prose_only)
    if not candidates:
        return NumericMatch(found=None, expected=expected, passed=False, error=None)

    best: Optional[NumericMatch] = None
    for candidate in candidates:
        # Ordered and deduped: a set here would make which of two equally-close
        # candidates gets reported depend on hash iteration order, and this
        # whole module is required to be deterministic.
        readings = [candidate.value]
        if candidate.as_fraction != candidate.value:
            readings.append(candidate.as_fraction)
        for value in readings:
            for use_abs in ((False, True) if match_abs else (False,)):
                probe = abs(value) if use_abs else value
                target = abs(expected) if use_abs else expected
                passed = within_tolerance(probe, target, rel=rel, abs_=abs_)
                error = abs(probe - target)
                better = (
                    best is None
                    or (passed and not best.passed)
                    or (passed == best.passed
                        and error < (best.error if best.error is not None else math.inf))
                )
                if better:
                    best = NumericMatch(
                        found=value, expected=expected, passed=passed,
                        error=error, raw=candidate.raw, used_abs=use_abs,
                    )
                if passed and not use_abs:
                    return best  # exact-sign match: nothing can beat it
    assert best is not None
    return best


def extract_labelled(text: str, labels: list[str], *,
                     window: int = 80) -> list[ParsedNumber]:
    """Numbers appearing within ``window`` characters after any of ``labels``.

    A precision tool for when a bare "any number matches" check is too loose —
    e.g. pinning the ATE specifically rather than accepting any number in a
    paragraph that also quotes a p-value and a sample size.
    """
    source = strip_non_prose(text)
    lowered = source.lower()
    spans: list[tuple[int, int]] = []
    for label in labels:
        needle = (label or "").strip().lower()
        if not needle:
            continue
        start = lowered.find(needle)
        while start != -1:
            spans.append((start, start + len(needle) + window))
            start = lowered.find(needle, start + 1)
    if not spans:
        return []
    return [n for n in extract_numbers(text)
            if any(lo <= n.start <= hi for lo, hi in spans)]
