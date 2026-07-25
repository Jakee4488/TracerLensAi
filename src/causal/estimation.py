"""Deterministic statistical causal inference via DoWhy.

ADK/Vertex-free: imports only stdlib + networkx at module load, and lazily
imports pandas/dowhy *inside* functions, so importing this module stays cheap
and the hermetic test core is unaffected. DoWhy cannot run in Gemini's code
sandbox (fixed library set) and cannot be an LLM FunctionTool (Vertex tool
isolation), so it runs here, called from the deterministic CausalEstimator.

Two halves, matching DoWhy's design:
- identification is symbolic and DATA-FREE — given the variable DAG + treatment
  + outcome it returns the backdoor/IV adjustment set. Runs on every effect
  query, dataset or not. This is the formal replacement for the LLM's ad-hoc
  confounder guessing.
- estimation + refutation need a DataFrame — run only when the message carried
  a parseable dataset (the exception, not the rule, for this project).

``run_identification`` never raises: any DoWhy failure degrades to a noted
IdentificationResult so the pipeline continues with LLM estimation.
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING, Optional

import networkx as nx

from src.causal.models import (
    CausalEstimand,
    EffectEstimate,
    IdentificationResult,
    RefutationResult,
    slugify,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    import pandas as pd


# ── Dataset extraction ───────────────────────────────────────────────────────

# The proxy wraps attachments as "--- Attached file: NAME ---\n<text>\n
# --- End of file: NAME ---" (proxy/main.py _attachment_context); also accept a
# fenced ```csv block for pasted tables.
_ATTACHMENT_BLOCK_RE = re.compile(
    r"---\s*Attached file:\s*(?P<name>.*?)\s*---\s*(?P<body>.*?)\s*---\s*End of file:",
    re.IGNORECASE | re.DOTALL,
)
_FENCED_RE = re.compile(r"```(?:csv|tsv|data|text)?[ \t]*\n(?P<body>.*?)```", re.DOTALL)


def _candidate_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for m in _ATTACHMENT_BLOCK_RE.finditer(text or ""):
        blocks.append(((m.group("name") or "").strip(), m.group("body") or ""))
    for m in _FENCED_RE.finditer(text or ""):
        blocks.append(("", m.group("body") or ""))
    return blocks


def parse_dataset(text: str, min_rows: int = 3, min_cols: int = 2) -> "Optional[pd.DataFrame]":
    """Best-effort DataFrame from a message that may carry an attached/pasted
    CSV or TSV. Column names are slugified so they line up with the estimand
    variable ids. Returns None when there's no usable tabular block (the common
    case here), so the pipeline runs identification-only."""
    import pandas as pd

    for name, body in _candidate_blocks(text or ""):
        body = body.strip()
        if not body or "\n" not in body:
            continue
        sep = "\t" if (name.lower().endswith(".tsv") or ("\t" in body and "," not in body)) else ","
        try:
            df = pd.read_csv(io.StringIO(body), sep=sep)
        except Exception:
            continue
        df = df.dropna(axis=1, how="all").dropna(axis=0, how="all")
        if df.shape[0] >= min_rows and df.shape[1] >= min_cols:
            df = df.rename(columns=lambda c: slugify(str(c)))
            return df.loc[:, ~df.columns.duplicated()]
    return None


# ── Graph construction ───────────────────────────────────────────────────────

def _acyclic_edges(edges: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Keep edges in order, dropping any that would introduce a cycle or a
    self-loop. Deterministic, so the same spec always yields the same DAG
    (DoWhy requires an acyclic graph)."""
    g = nx.DiGraph()
    kept: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for s, t in edges:
        if s == t or (s, t) in seen:
            continue
        g.add_edge(s, t)
        if not nx.is_directed_acyclic_graph(g):
            g.remove_edge(s, t)
            continue
        seen.add((s, t))
        kept.append((s, t))
    return kept


def build_causal_graph(estimand: CausalEstimand) -> tuple[str, list[str], list[tuple[str, str]]]:
    """A GML string DoWhy can consume, plus the cleaned node/edge lists.

    GML via networkx avoids the pydot/graphviz system dependency a DOT string
    would pull in. Treatment/outcome are always present as nodes."""
    nodes = [v.id for v in estimand.variables]
    node_set = set(nodes)
    for extra in (estimand.treatment, estimand.outcome):
        if extra and extra not in node_set:
            nodes.append(extra)
            node_set.add(extra)

    raw = [(e.source, e.target) for e in estimand.edges
           if e.source in node_set and e.target in node_set]
    edges = _acyclic_edges(raw)

    g = nx.DiGraph()
    g.add_nodes_from(nodes)
    g.add_edges_from(edges)
    gml = "\n".join(nx.generate_gml(g))
    return gml, nodes, edges


# ── Identification + estimation (DoWhy) ──────────────────────────────────────

def _extract_identification(estimand: CausalEstimand, identified) -> IdentificationResult:
    """Pull the adjustment set / estimand type off DoWhy's IdentifiedEstimand,
    defensively (its accessor surface has drifted across versions)."""
    treatment, outcome = estimand.treatment, estimand.outcome
    backdoor: list[str] = []
    instruments: list[str] = []
    try:
        backdoor = list(identified.get_backdoor_variables() or [])
    except Exception:
        pass
    try:
        instruments = list(identified.get_instrumental_variables() or [])
    except Exception:
        pass

    estimands = getattr(identified, "estimands", {}) or {}

    def _has(kind: str) -> bool:
        entry = estimands.get(kind)
        return bool(entry) and entry.get("estimand") is not None

    if backdoor or _has("backdoor"):
        estimand_type = "backdoor"
    elif instruments or _has("iv"):
        estimand_type = "iv"
    elif _has("frontdoor"):
        estimand_type = "frontdoor"
    else:
        estimand_type = "none"

    identifiable = estimand_type != "none" or bool(backdoor)

    expr = ""
    try:
        entry = estimands.get(estimand_type) or {}
        expr = str(entry.get("realized_estimand_expr") or entry.get("estimand") or "")
    except Exception:
        expr = ""
    if not expr:
        adj = ", ".join(sorted(backdoor)) if backdoor else "no adjustment needed"
        expr = f"E[{outcome} | do({treatment})]; adjust for: {adj}"

    return IdentificationResult(
        treatment=treatment,
        outcome=outcome,
        identifiable=identifiable,
        estimand_type=estimand_type,
        adjustment_set=sorted(backdoor),
        instruments=sorted(instruments),
        estimand_expr=expr,
    )


def _method_for(ident: IdentificationResult) -> str:
    if ident.estimand_type == "iv" and ident.instruments:
        return "iv.instrumental_variable"
    return "backdoor.linear_regression"


def _estimate(model, identified, ident: IdentificationResult, df) -> EffectEstimate:
    needed = {ident.treatment, ident.outcome, *ident.adjustment_set, *ident.instruments}
    missing = sorted(c for c in needed if c and c not in df.columns)
    if missing:
        return EffectEstimate(method="", note=f"dataset missing columns: {', '.join(missing)}")

    method = _method_for(ident)
    estimate = model.estimate_effect(
        identified, method_name=method, test_significance=True, confidence_intervals=True,
    )
    point = _scalar(estimate.value)

    ci_low = ci_high = None
    try:
        ci_low, ci_high = _ci_pair(estimate.get_confidence_intervals())
    except Exception:
        pass

    return EffectEstimate(
        method=method,
        point=point,
        ci_low=ci_low,
        ci_high=ci_high,
        n_obs=int(df.shape[0]),
        refutations=_refute(model, identified, estimate, point),
    )


def _refute(model, identified, estimate, point: float) -> list[RefutationResult]:
    """Two standard robustness checks. A random common cause should barely move
    the estimate; a placebo treatment should drive it to ~0."""
    tol = max(0.15 * abs(point), 1e-6)
    checks = (
        ("random_common_cause", lambda new: abs(new - point) <= tol),
        ("placebo_treatment_refuter", lambda new: abs(new) <= tol),
    )
    out: list[RefutationResult] = []
    for method, ok in checks:
        try:
            res = model.refute_estimate(identified, estimate, method_name=method)
            new_effect = _scalar(res.new_effect)
            out.append(RefutationResult(
                method=method, original_effect=point,
                new_effect=new_effect, passed=bool(ok(new_effect)),
            ))
        except Exception:
            continue
    return out


def run_identification(
    estimand: CausalEstimand, df=None
) -> tuple[IdentificationResult, Optional[EffectEstimate]]:
    """Identify (always) then estimate+refute (only with a usable dataset).

    Never raises: DoWhy/pandas absence or any modeling error degrades to a noted
    IdentificationResult, so the causal pipeline keeps working (the LLM still
    produces a magnitude, now grounded on whatever identification succeeded)."""
    if (not estimand or not estimand.treatment or not estimand.outcome
            or estimand.treatment == estimand.outcome):
        return IdentificationResult(
            treatment=getattr(estimand, "treatment", ""),
            outcome=getattr(estimand, "outcome", ""),
            identifiable=False, estimand_type="none",
            note="treatment and outcome must be distinct, named variables",
        ), None

    try:
        import pandas as pd
        from dowhy import CausalModel
    except Exception as exc:  # dowhy/pandas not installed
        return IdentificationResult(
            treatment=estimand.treatment, outcome=estimand.outcome,
            identifiable=False, estimand_type="none",
            note=f"statistical inference unavailable ({type(exc).__name__})",
        ), None

    has_data = df is not None and not getattr(df, "empty", True)
    try:
        gml, nodes, _edges = build_causal_graph(estimand)
        data = df if has_data else pd.DataFrame({n: [0.0, 1.0] for n in nodes})
        model = CausalModel(
            data=data, treatment=estimand.treatment, outcome=estimand.outcome, graph=gml,
        )
        identified = model.identify_effect(proceed_when_unidentifiable=True)
        ident = _extract_identification(estimand, identified)
    except Exception as exc:
        return IdentificationResult(
            treatment=estimand.treatment, outcome=estimand.outcome,
            identifiable=False, estimand_type="none",
            note=f"identification failed ({type(exc).__name__}: {exc})"[:280],
        ), None

    if not has_data:
        return ident, None
    try:
        effect = _estimate(model, identified, ident, df)
    except Exception as exc:
        effect = EffectEstimate(method="", note=f"estimation failed ({type(exc).__name__})")
    return ident, effect


# ── Numeric normalization (DoWhy returns scalars, numpy arrays, or nested) ────

def _scalar(v) -> float:
    try:
        import numpy as np
        if isinstance(v, np.ndarray):
            return float(v.reshape(-1)[0])
    except Exception:
        pass
    if isinstance(v, (list, tuple)):
        return _scalar(v[0])
    return float(v)


def _ci_pair(ci) -> tuple[float, float]:
    try:
        import numpy as np
        arr = np.array(ci, dtype=float).reshape(-1)
        return float(arr[0]), float(arr[-1])
    except Exception:
        seq = list(ci)
        flat = seq[0] if seq and isinstance(seq[0], (list, tuple)) else seq
        return float(flat[0]), float(flat[-1])
