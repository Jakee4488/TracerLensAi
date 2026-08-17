"""Deterministic eval checks: arithmetic accuracy and causal node-path shape.

Zero LLM calls. Where `metrics.py` asks a judge for an *opinion* about whether
an answer is good, this module *asserts* — a stated ATE either lands within
tolerance of the known ground truth or it does not, and the reasoning either
passed through the expected causal nodes or it did not. Both are facts about
the trace, so they are computed, not graded.

Two data sources per case:

- **The final answer text** — numbers parsed with `src.causal.numeric`, the
  same parser the step controller uses, so the grader and the agent never
  disagree about what number was written.
- **Node traces** — the fenced ```causal-nodes``` block emitted by
  `CausalNodeTraceEmitter` when CAUSAL_NODE_TRACE=1. These carry typed floats
  straight from the estimator (`effect.point`, `counterfactual.delta`), so an
  arithmetic check can target an *intermediate* result rather than re-parsing
  prose. They also carry each node's adjustment set, which is what makes the
  mediator/collider assertions structural instead of rhetorical.

Expectations live in `expectations.json`, keyed by `eval_case_id`. Because the
grading `instance` does not carry the case id, cases are resolved by matching
the prompt text back to the dataset files.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent

# agents-cli loads this file by path; the repo root is not guaranteed to be on
# sys.path the way it is under pytest.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.causal.numeric import find_match, within_tolerance  # noqa: E402

EXPECTATIONS_PATH = _HERE / "expectations.json"
DATASETS_DIR = _HERE / "datasets"

# Global backstop when neither the check nor the metric declares a tolerance.
_FALLBACK_TOLERANCE = {"rel": 0.20, "abs": 0.10}

_NODE_BLOCK_RE = re.compile(r"```causal-nodes\s*(\{.*?\})\s*```", re.DOTALL)
_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


# ── Loading ──────────────────────────────────────────────────────────────────

def load_expectations(path: Optional[Path] = None) -> dict:
    target = Path(path) if path else EXPECTATIONS_PATH
    if not target.exists():
        return {"tolerance_defaults": {}, "cases": {}}
    with open(target, encoding="utf-8") as handle:
        return json.load(handle)


def _normalize(text: str) -> str:
    return _NORMALIZE_RE.sub("_", (text or "").strip().lower()).strip("_")


def _case_prompts(datasets_dir: Optional[Path] = None) -> dict[str, str]:
    """``eval_case_id -> normalized prompt text`` across every dataset file."""
    directory = Path(datasets_dir) if datasets_dir else DATASETS_DIR
    index: dict[str, str] = {}
    for path in sorted(directory.glob("*.json")):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        for case in data.get("eval_cases") or []:
            case_id = case.get("eval_case_id")
            if not case_id:
                continue
            parts = ((case.get("prompt") or {}).get("parts")) or []
            text = " ".join(str(p.get("text", "")) for p in parts)
            if text.strip():
                index[case_id] = _normalize(text)
    return index


def resolve_case_id(prompt: str, datasets_dir: Optional[Path] = None) -> Optional[str]:
    """Which dataset case this grading instance came from.

    The grader's `instance` carries the prompt but not the case id, so the
    prompt is matched back to the datasets. Exact normalized equality first;
    then containment, which covers a grader that trims or re-wraps long
    prompts (the CSV cases are thousands of characters).
    """
    needle = _normalize(prompt)
    if not needle:
        return None
    index = _case_prompts(datasets_dir)
    for case_id, text in index.items():
        if text == needle:
            return case_id
    best: Optional[tuple[int, str]] = None
    for case_id, text in index.items():
        if text and (text in needle or needle in text):
            overlap = min(len(text), len(needle))
            if best is None or overlap > best[0]:
                best = (overlap, case_id)
    return best[1] if best else None


# ── Node-trace access ────────────────────────────────────────────────────────

def _iter_text_fragments(agent_data: Any):
    """Every text fragment in a grading instance's trace, whatever its shape.

    `agent_data` arrives as a JSON string in practice, but is a dict when a
    test constructs it, and the event shape has varied across CLI versions —
    so this walks defensively rather than assuming one layout.
    """
    if agent_data is None:
        return
    if isinstance(agent_data, str):
        text = agent_data
        try:
            agent_data = json.loads(text)
        except ValueError:
            yield text
            return
    if isinstance(agent_data, dict):
        for turn in agent_data.get("turns") or []:
            for event in turn.get("events") or []:
                content = event.get("content") or {}
                for part in content.get("parts") or []:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        yield part["text"]


def extract_node_traces(instance: dict) -> list[dict]:
    """Node traces for this case, from wherever the block landed.

    Searched in the trace first and the final response second: the emitter runs
    last, so its block is normally the final event, but whether that text also
    becomes the graded `response` depends on how the response is assembled.
    Scanning both means the check does not depend on that detail.
    """
    sources: list[str] = list(_iter_text_fragments(instance.get("agent_data")))
    response = instance.get("response")
    if isinstance(response, str):
        sources.append(response)

    nodes: list[dict] = []
    seen: set[tuple] = set()
    for text in sources:
        for match in _NODE_BLOCK_RE.finditer(text or ""):
            try:
                payload = json.loads(match.group(1))
            except ValueError:
                continue
            for node in payload.get("nodes") or []:
                key = (node.get("seq"), node.get("node_id"), node.get("node_kind"))
                if key in seen:
                    continue
                seen.add(key)
                nodes.append(node)
    return sorted(nodes, key=lambda n: int(n.get("seq") or 0))


def node_values(nodes: list[dict]) -> dict[str, float]:
    """``"<node_id>.<value>" -> float`` across every node trace."""
    flat: dict[str, float] = {}
    for node in nodes:
        node_id = str(node.get("node_id", ""))
        for name, value in (node.get("values") or {}).items():
            try:
                flat[f"{node_id}.{name}"] = float(value)
            except (TypeError, ValueError):
                continue
    return flat


# Below this length, containment matching is more dangerous than useful: the
# pattern "m" would match every id containing the letter m.
_MIN_FUZZY_LEN = 3


def _id_matches(candidate: str, pattern: str) -> bool:
    """Tolerant id comparison.

    Node and variable ids are slugified LLM output: the confounder the dataset
    calls `season` may arrive as `season_indicator`. Exact matching would make
    these checks fail on paraphrase rather than on substance, so normalized
    containment counts either way.

    Very short patterns fall back to exact match. A one- or two-character
    pattern is a substring of almost anything — `"m"` for a mediator would match
    an adjustment set containing `income`, failing an exclusion check over a
    letter rather than a variable. Prefer names of >= 3 characters in
    expectations so the fuzzy path stays available.
    """
    left, right = _normalize(candidate), _normalize(pattern)
    if not left or not right:
        return False
    if len(right) < _MIN_FUZZY_LEN or len(left) < _MIN_FUZZY_LEN:
        return left == right
    return left == right or left in right or right in left


def _any_matches(candidates: list, pattern: str) -> bool:
    return any(_id_matches(str(c), pattern) for c in candidates or [])


# ── Tolerance resolution ─────────────────────────────────────────────────────

def resolve_tolerance(check: dict, metric_name: str, expectations: dict) -> dict:
    """Per-check tolerance, else the metric's default, else the global backstop.

    Three levels because the right band genuinely differs by target: a DoWhy
    point estimate off a 60-row sample deserves a tighter bound than a number
    an LLM rounded into a sentence, and both are checked in the same run.
    """
    defaults = (expectations.get("tolerance_defaults") or {})
    resolved = dict(_FALLBACK_TOLERANCE)
    resolved.update(defaults.get(metric_name) or {})
    resolved.update(check.get("tolerance") or {})
    return resolved


def _bounds(tolerance: dict) -> tuple[Optional[float], Optional[float]]:
    rel = tolerance.get("rel")
    abs_ = tolerance.get("abs")
    return (float(rel) if rel is not None else None,
            float(abs_) if abs_ is not None else None)


# ── Check runners ────────────────────────────────────────────────────────────

def run_numeric_checks(instance: dict, case: dict, expectations: dict,
                       metric_name: str = "numeric_accuracy") -> list[dict]:
    """Assert each declared numeric ground truth against answer and/or nodes."""
    checks = case.get("numeric") or []
    if not checks:
        return []

    nodes = extract_node_traces(instance)
    values = node_values(nodes)
    response = instance.get("response") or ""

    results: list[dict] = []
    for check in checks:
        name = str(check.get("name") or "value")
        expected = check.get("expected")
        source = str(check.get("source") or "answer")
        tolerance = resolve_tolerance(check, metric_name, expectations)
        rel, abs_ = _bounds(tolerance)

        if expected is None:
            results.append({"name": name, "source": source, "passed": False,
                            "detail": "expectation declares no `expected` value"})
            continue
        expected = float(expected)

        if source.startswith("node:"):
            key = source[len("node:"):]
            if not nodes:
                results.append({
                    "name": name, "source": source, "passed": False,
                    "detail": ("no node traces in this trace — run the agent with "
                               "CAUSAL_NODE_TRACE=1 for node-level checks"),
                    "skipped_reason": "no_node_traces",
                })
                continue
            if key not in values:
                results.append({"name": name, "source": source, "passed": False,
                                "detail": f"node value '{key}' absent; "
                                          f"available: {', '.join(sorted(values)) or 'none'}"})
                continue
            actual = values[key]
            passed = within_tolerance(actual, expected, rel=rel, abs_=abs_)
            results.append({
                "name": name, "source": source, "passed": passed,
                "expected": expected, "found": actual,
                "detail": f"{key}={actual:.6g} vs expected {expected:.6g} "
                          f"(rel={rel}, abs={abs_})",
            })
            continue

        match = find_match(
            response, expected, rel=rel, abs_=abs_,
            match_abs=bool(check.get("match_abs", False)),
        )
        results.append({
            "name": name, "source": source, "passed": match.passed,
            "expected": expected, "found": match.found,
            "detail": (f"closest number in answer {match.found!r} vs expected "
                       f"{expected:.6g} (rel={rel}, abs={abs_})"
                       if match.found is not None
                       else "no numeric literal found in the answer"),
        })
    return results


def run_node_checks(instance: dict, case: dict) -> list[dict]:
    """Assert the reasoning passed through the expected causal nodes."""
    spec = case.get("nodes") or {}
    if not spec:
        return []

    nodes = extract_node_traces(instance)
    if not nodes:
        return [{
            "name": "node_traces_present", "passed": False,
            "detail": ("no ```causal-nodes``` block in the trace — run the agent "
                       "with CAUSAL_NODE_TRACE=1"),
            "skipped_reason": "no_node_traces",
        }]

    visited = [str(n.get("node_id", "")) for n in nodes]
    kinds = {str(n.get("node_kind", "")) for n in nodes}
    results: list[dict] = []

    for kind in spec.get("require_kinds") or []:
        results.append({
            "name": f"kind:{kind}", "passed": kind in kinds,
            "detail": f"node kinds present: {', '.join(sorted(kinds)) or 'none'}",
        })

    for node_id in spec.get("expect_visited") or []:
        results.append({
            "name": f"visited:{node_id}", "passed": _any_matches(visited, node_id),
            "detail": f"visited nodes: {', '.join(visited) or 'none'}",
        })

    for node_id in spec.get("forbid_visited") or []:
        results.append({
            "name": f"not-visited:{node_id}", "passed": not _any_matches(visited, node_id),
            "detail": f"visited nodes: {', '.join(visited) or 'none'}",
        })

    # Ordered traversal: a subsequence, not equality — extra nodes between the
    # required ones are legitimate (a replan retry, an extra plan step), but
    # the required ones must appear in the declared order.
    expected_order = spec.get("expect_order") or []
    if expected_order:
        results.append({
            "name": "order:" + "->".join(expected_order),
            "passed": _order_satisfied(visited, expected_order),
            "detail": f"visited order: {' -> '.join(visited) or 'none'}",
        })

    # Adjustment-set assertions read the identification node's own output, so
    # "did not adjust for the mediator" is checked against the formal estimand
    # rather than against whatever the prose claimed.
    ident = next((n for n in nodes if n.get("node_kind") == "identification"), None)
    adjustment_set = list(((ident or {}).get("outputs") or {}).get("adjustment_set") or [])
    includes = spec.get("adjustment_set_includes") or []
    excludes = spec.get("adjustment_set_excludes") or []
    # "The correct adjustment set is EMPTY" is a real structural claim, not the
    # absence of one — it is the right answer for full mediation, for a pure
    # collider, and for reverse causation, and an agent that adjusts for
    # anything at all has got it wrong. Expressing it only as a numeric check on
    # adjustment_set_size would hide structural ground truth in a number.
    if spec.get("adjustment_set_empty") is not None:
        want_empty = bool(spec["adjustment_set_empty"])
        is_empty = ident is not None and not adjustment_set
        results.append({
            "name": "adjustment_set_empty", "passed": is_empty == want_empty,
            "detail": (f"adjustment set: {', '.join(adjustment_set) or 'empty'}"
                       if ident is not None else "no identification node in the trace"),
        })

    if includes or excludes:
        if ident is None:
            results.append({
                "name": "identification_node", "passed": False,
                "detail": "no identification node in the trace (estimand stage did not run)",
            })
        else:
            rendered = ", ".join(adjustment_set) or "empty"
            for var in includes:
                results.append({
                    "name": f"adjust-for:{var}",
                    "passed": _any_matches(adjustment_set, var),
                    "detail": f"adjustment set: {rendered}",
                })
            for var in excludes:
                results.append({
                    "name": f"never-adjust-for:{var}",
                    "passed": not _any_matches(adjustment_set, var),
                    "detail": f"adjustment set: {rendered}",
                })

    expected_type = spec.get("estimand_type")
    if expected_type:
        actual_type = ((ident or {}).get("outputs") or {}).get("estimand_type") or ""
        results.append({
            "name": f"estimand_type:{expected_type}",
            "passed": _normalize(str(actual_type)) == _normalize(str(expected_type)),
            "detail": f"estimand type: {actual_type or 'none'}",
        })

    if spec.get("require_identifiable") is not None:
        want = bool(spec["require_identifiable"])
        got = bool(((ident or {}).get("outputs") or {}).get("identifiable"))
        results.append({
            "name": f"identifiable={want}", "passed": got == want,
            "detail": f"identifiable: {got}",
        })
    return results


def _order_satisfied(visited: list[str], expected_order: list[str]) -> bool:
    cursor = 0
    for node_id in expected_order:
        found = False
        while cursor < len(visited):
            if _id_matches(visited[cursor], node_id):
                found = True
                cursor += 1
                break
            cursor += 1
        if not found:
            return False
    return True


# ── Scoring ──────────────────────────────────────────────────────────────────

def score_results(results: list[dict], kind: str) -> dict:
    """Fraction of checks passed, with a per-check breakdown in `explanation`.

    A case with nothing declared scores 1.0 and says so. That is *absence of a
    check*, not evidence of correctness — read the explanation before treating
    a 1.0 as a pass.
    """
    if not results:
        return {"score": 1.0,
                "explanation": f"[{kind}] no expectations declared for this case (not applicable)"}

    passed = [r for r in results if r.get("passed")]
    failed = [r for r in results if not r.get("passed")]
    score = len(passed) / len(results)

    lines = [f"[{kind}] {len(passed)}/{len(results)} checks passed"]
    for result in failed:
        lines.append(f"  FAIL {result.get('name')}: {result.get('detail', '')}")
    for result in passed:
        lines.append(f"  ok   {result.get('name')}: {result.get('detail', '')}")
    return {"score": round(score, 4), "explanation": "\n".join(lines)}


def evaluate_case(instance: dict, kind: str,
                  expectations_path: Optional[Path] = None,
                  datasets_dir: Optional[Path] = None) -> dict:
    """Entry point shared by both deterministic metrics in `metrics.py`."""
    expectations = load_expectations(expectations_path)
    case_id = resolve_case_id(instance.get("prompt") or "", datasets_dir)
    if case_id is None:
        return {"score": 1.0,
                "explanation": f"[{kind}] prompt did not match any dataset case (not applicable)"}

    case = (expectations.get("cases") or {}).get(case_id) or {}
    if not case:
        return {"score": 1.0,
                "explanation": f"[{kind}] case '{case_id}' declares no expectations (not applicable)"}

    if kind == "numeric_accuracy":
        results = run_numeric_checks(instance, case, expectations, metric_name=kind)
    else:
        results = run_node_checks(instance, case)
    scored = score_results(results, kind)
    scored["explanation"] = f"case={case_id} " + scored["explanation"]
    return scored


def node_trace_enabled() -> bool:
    """Whether the agent was run with node-trace emission on."""
    return os.environ.get("CAUSAL_NODE_TRACE") == "1"
