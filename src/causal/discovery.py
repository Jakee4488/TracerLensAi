"""Data-driven correction of the LLM-asserted variable DAG (causal discovery).

The estimand-spec LLM asserts a variable-level causal graph from world knowledge;
DoWhy then identifies on that assumed graph. When a dataset is available (attached
or web-fetched) this module lets the *data* talk back: it runs constraint-based
discovery (causal-learn PC, FisherZ) for the skeleton and a non-Gaussian
orientation model (DirectLiNGAM) for edge direction, then **conservatively**
reconciles the two against the LLM prior — the LLM edge is kept unless the data
disagrees strongly *and* directionally. Every edit is logged.

Design, like ``estimation.py``:
- ADK/Vertex-free; ``causallearn`` is imported lazily *inside* ``reconcile_graph``
  so importing this module stays cheap and the hermetic test core is unaffected.
- ``reconcile_graph`` never raises: any failure (lib missing, too little data,
  numerical error) degrades to ``None`` and the DAG is used exactly as asserted.

The single discovery pass also *detects* inconsistency — the skeleton's
independence tests are the same signal a standalone falsification check would
give — so a ``consistent`` / ``untestable`` verdict falls out for free.
"""

from __future__ import annotations

from typing import Optional

from src.causal.estimation import _acyclic_edges, build_causal_graph
from src.causal.models import CausalEstimand, GraphChange, GraphReconciliation, VarEdge

# Caps: constraint-based CI tests are O(vars^2 * conditioning); keep them cheap
# and statistically meaningful.
_MAX_VARS = 12
_MAX_ROWS = 500
_MIN_ROWS = 30
_ALPHA = 0.05


def reconcile_graph(estimand: CausalEstimand, df) -> Optional[GraphReconciliation]:
    """Test the LLM DAG against ``df`` and conservatively correct it.

    Returns None (never raises) when discovery cannot run — no data, causal-learn
    absent, or a numerical failure — so the caller keeps the asserted DAG."""
    if df is None or getattr(df, "empty", True):
        return None
    if not estimand or not estimand.treatment or not estimand.outcome:
        return None

    try:
        import pandas as pd
    except Exception:
        return None

    try:
        # Prior DAG (already deduped/acyclic/slugified). Edges touching variables
        # that are NOT observed columns are preserved untouched (only observed
        # variables are testable).
        _gml, _nodes, prior_edges = build_causal_graph(estimand)

        # Testable = numeric observed columns (union of estimand vars and data
        # columns, so an omitted-but-measured confounder can be discovered).
        numeric = df.apply(pd.to_numeric, errors="coerce")
        numeric = numeric.dropna(axis=1, how="all")

        # Fill the cap with the variables the estimand actually asks about
        # before falling back to column order. The cap used to be purely
        # positional, so on a wide dataset the outcome could sit past the
        # twelfth column and be excluded from discovery altogether — the run
        # would then report a confident "corrected" verdict derived only from
        # nuisance variables, having never tested the edge under question.
        # Column names and variable ids share a namespace: both are slugified.
        observed = list(numeric.columns)
        preferred = [c for c in ([estimand.treatment, estimand.outcome]
                                 + [v.id for v in estimand.variables])
                     if c in observed]
        testable = list(dict.fromkeys(preferred + observed))[:_MAX_VARS]
        note_bits: list[str] = []
        if numeric.shape[1] > _MAX_VARS:
            note_bits.append(f"capped to {_MAX_VARS} of {numeric.shape[1]} columns")

        untouched = [(s, t) for s, t in prior_edges
                     if s not in testable or t not in testable]
        prior_testable = [(s, t) for s, t in prior_edges
                          if s in testable and t in testable]

        data = numeric[testable].dropna()
        if len(testable) < 2 or data.shape[0] < _MIN_ROWS:
            return GraphReconciliation(
                verdict="untestable",
                corrected_edges=[VarEdge(source=s, target=t) for s, t in prior_edges],
                note=(f"too few rows ({data.shape[0]}<{_MIN_ROWS})"
                      if data.shape[0] < _MIN_ROWS else "fewer than 2 observed variables"),
            )
        if data.shape[0] > _MAX_ROWS:
            data = data.sample(_MAX_ROWS, random_state=0)

        arr = data.to_numpy(dtype=float)
        rel = _pc_relations(arr, testable)          # unordered-pair -> relation
        order_pos = _lingam_order(arr, testable)    # var -> upstream rank (or {})
        if rel is None or not order_pos:
            return GraphReconciliation(
                verdict="untestable",
                corrected_edges=[VarEdge(source=s, target=t) for s, t in prior_edges],
                note="discovery produced no usable structure",
            )
    except Exception:
        return None

    def before(a: str, b: str) -> bool:
        return order_pos.get(a, 1e9) < order_pos.get(b, -1e9)

    latent: set = set()
    proposed: list[tuple[str, str]] = list(untouched)
    handled: set = set()

    # 1) Decide each prior edge a->b among testable variables. Conservative:
    #    a reversal needs the skeleton to be adjacent (not contradicting) AND
    #    LiNGAM to orient the other way; otherwise the LLM prior wins.
    for a, b in prior_testable:
        handled.add(frozenset((a, b)))
        r = rel.get(frozenset((a, b)), "none")
        if r == "bidirected":
            latent.add(tuple(sorted((a, b))))
            proposed.append((a, b))                 # keep; flag latent confounding
            continue
        if r == "none":
            continue                                # data says independent -> drop
        oriented = _resolve(r, a, b)                # PC orientation, or None if undirected
        if oriented == (a, b):
            proposed.append((a, b))                 # PC confirms the prior direction
        elif oriented == (b, a) and before(b, a):
            proposed.append((b, a))                 # PC + LiNGAM both reverse it
        elif oriented is None and before(b, a):
            proposed.append((b, a))                 # adjacent but undirected; LiNGAM reverses
        else:
            proposed.append((a, b))                 # keep the LLM prior

    # 2) Discover NEW edges the LLM missed (adjacency present, not in prior).
    for i, a in enumerate(testable):
        for b in testable[i + 1:]:
            key = frozenset((a, b))
            if key in handled:
                continue
            r = rel.get(key, "none")
            if r == "none":
                continue
            if r == "bidirected":
                latent.add(tuple(sorted((a, b))))
                continue
            oriented = _resolve(r, a, b)
            if oriented is not None:
                s, t = oriented
                if before(s, t):
                    proposed.append((s, t))
            elif before(a, b):
                proposed.append((a, b))
            elif before(b, a):
                proposed.append((b, a))

    corrected = _acyclic_edges(proposed)
    changes = _diff(prior_edges, corrected)
    verdict = "corrected" if changes else "consistent"
    return GraphReconciliation(
        verdict=verdict,
        n_changes=len(changes),
        changes=changes,
        corrected_edges=[VarEdge(source=s, target=t) for s, t in corrected],
        latent_confounders=[f"{a}<->{b}" for a, b in sorted(latent)],
        note="; ".join(note_bits),
    )


# ── causal-learn adapters (defensive: version/encoding drift) ─────────────────

def _pc_relations(arr, cols: list[str]) -> Optional[dict]:
    """Run PC (FisherZ) and return {frozenset({a,b}): relation}, where relation
    is one of 'none', 'undirected', 'bidirected', or an oriented mark relative to
    the pair as stored (resolved later by _resolve). None on failure."""
    try:
        from causallearn.graph.Endpoint import Endpoint
        from causallearn.search.ConstraintBased.PC import pc
    except Exception:
        return None
    try:
        cg = pc(arr, _ALPHA, "fisherz", stable=True, show_progress=False, node_names=list(cols))
    except Exception:
        return None

    rel: dict = {}
    try:
        for edge in cg.G.get_graph_edges():
            n1 = edge.get_node1().get_name()
            n2 = edge.get_node2().get_name()
            e1 = edge.get_endpoint1()
            e2 = edge.get_endpoint2()
            key = frozenset((n1, n2))
            if e1 == Endpoint.ARROW and e2 == Endpoint.ARROW:
                rel[key] = "bidirected"
            elif e1 == Endpoint.TAIL and e2 == Endpoint.ARROW:
                rel[key] = f"->:{n1}:{n2}"       # n1 -> n2
            elif e1 == Endpoint.ARROW and e2 == Endpoint.TAIL:
                rel[key] = f"->:{n2}:{n1}"       # n2 -> n1
            else:
                rel[key] = "undirected"
    except Exception:
        return None
    return rel


def _resolve(r: str, a: str, b: str) -> Optional[tuple[str, str]]:
    """Map a stored relation to an oriented (source, target) for the pair (a, b),
    or None when PC left it undirected."""
    if not r or not r.startswith("->:"):
        return None
    _, s, t = r.split(":", 2)
    if {s, t} == {a, b}:
        return (s, t)
    return None


def _lingam_order(arr, cols: list[str]) -> dict:
    """DirectLiNGAM causal order → {var: upstream rank}. Empty dict on failure
    (non-Gaussian assumption; on failure discovery falls back to PC-only, which
    keeps the conservative LLM prior for undirected pairs)."""
    try:
        from causallearn.search.FCMBased import lingam
    except Exception:
        return {}
    try:
        model = lingam.DirectLiNGAM()
        model.fit(arr)
        return {cols[idx]: pos for pos, idx in enumerate(list(model.causal_order_))}
    except Exception:
        return {}


def _diff(prior: list[tuple[str, str]], corrected: list[tuple[str, str]]) -> list[GraphChange]:
    """Derive the change list from prior vs corrected edge sets."""
    p = set(prior)
    c = set(corrected)
    changes: list[GraphChange] = []
    for s, t in sorted(p - c):
        if (t, s) in c:
            changes.append(GraphChange(kind="reverse", source=s, target=t,
                                       reason="data orients this edge the other way"))
        else:
            changes.append(GraphChange(kind="remove", source=s, target=t,
                                       reason="data finds these conditionally independent"))
    for s, t in sorted(c - p):
        if (t, s) in p:
            continue  # already reported as a reverse
        changes.append(GraphChange(kind="add", source=s, target=t,
                                   reason="data supports this edge (missed by the stated graph)"))
    return changes
