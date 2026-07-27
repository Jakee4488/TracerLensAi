"""Tests for data-driven DAG correction (causal discovery).

The 'money' tests prove the guardrail: a correct DAG on matching SCM data is
left untouched, while a wrong DAG (reversed edge / omitted confounder) on the
*same* data is corrected so that DoWhy then identifies the right adjustment set.
They ``importorskip('causallearn')`` (+ dowhy for the identification assertion)
so the core suite stays hermetic when the discovery libs are absent.

The generating SCM is z -> x, z -> y, x -> y with **non-Gaussian (uniform)**
noise (DirectLiNGAM needs non-Gaussianity to orient) and coefficients chosen so
the model is faithful (no accidental independence that would make PC drop a true
edge).
"""

import pytest

from src.causal.discovery import reconcile_graph
from src.causal.models import CausalEstimand, CausalVariable, GraphReconciliation, VarEdge


def _spec(treatment, outcome, variables, edges):
    return CausalEstimand(
        treatment=treatment,
        outcome=outcome,
        variables=[CausalVariable(id=v, role=r) for v, r in variables],
        edges=[VarEdge(source=s, target=t) for s, t in edges],
    )


def _scm_df(np, pd, n=1500, seed=0):
    rng = np.random.default_rng(seed)
    z = rng.uniform(-2, 2, size=n)
    x = 0.8 * z + rng.uniform(-2, 2, size=n)
    y = 2.0 * x - 1.2 * z + rng.uniform(-2, 2, size=n)  # z confounds x -> y
    return pd.DataFrame({"x": x, "y": y, "z": z})


# The three canonical DAGs over {x, y, z} the LLM might assert.
_CORRECT = (("x", "treatment"), ("y", "outcome"), ("z", "confounder")), \
           (("z", "x"), ("z", "y"), ("x", "y"))
_OMITTED = (("x", "treatment"), ("y", "outcome")), (("x", "y"),)          # z missing
_REVERSED = (("x", "treatment"), ("y", "outcome"), ("z", "confounder")), \
            (("z", "x"), ("z", "y"), ("y", "x"))                          # x<->y flipped


# ── No-data / guard paths (no discovery lib needed) ──────────────────────────

def test_reconcile_returns_none_without_data():
    spec = _spec("x", "y", _CORRECT[0], _CORRECT[1])
    assert reconcile_graph(spec, None) is None


def test_reconcile_untestable_on_too_few_rows():
    pd = pytest.importorskip("pandas")
    pytest.importorskip("numpy")
    df = pd.DataFrame({"x": [1.0, 2, 3], "y": [2.0, 4, 6], "z": [0.1, 0.2, 0.3]})
    spec = _spec("x", "y", _CORRECT[0], _CORRECT[1])
    recon = reconcile_graph(spec, df)
    assert isinstance(recon, GraphReconciliation)
    assert recon.verdict == "untestable"
    # The graph is preserved untouched for the untestable case.
    assert {(e.source, e.target) for e in recon.corrected_edges} == set(_CORRECT[1])


# ── Money tests (discovery libs required) ────────────────────────────────────

def test_correct_dag_is_left_uncorrected():
    pytest.importorskip("causallearn")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    spec = _spec("x", "y", _CORRECT[0], _CORRECT[1])
    recon = reconcile_graph(spec, _scm_df(np, pd))
    assert recon is not None
    assert recon.verdict != "corrected"          # data agrees with the true graph
    assert recon.n_changes == 0


def test_reversed_edge_is_corrected_to_the_truth():
    pytest.importorskip("causallearn")
    dowhy = pytest.importorskip("dowhy")  # noqa: F841 - identification assertion below
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    from src.causal.estimation import run_identification
    from src.causal.estimator import _apply_corrected_edges

    df = _scm_df(np, pd)
    spec = _spec("x", "y", _REVERSED[0], _REVERSED[1])
    recon = reconcile_graph(spec, df)

    assert recon is not None and recon.verdict == "corrected"
    assert any(c.kind == "reverse" for c in recon.changes)
    # x -> y is restored (not y -> x) in the corrected edge set.
    corrected = {(e.source, e.target) for e in recon.corrected_edges}
    assert ("x", "y") in corrected and ("y", "x") not in corrected
    # And identification on the corrected graph recovers the confounder.
    ident, _ = run_identification(_apply_corrected_edges(spec, recon.corrected_edges), df)
    assert ident.identifiable and "z" in ident.adjustment_set


def test_omitted_confounder_is_discovered():
    pytest.importorskip("causallearn")
    dowhy = pytest.importorskip("dowhy")  # noqa: F841
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    from src.causal.estimation import run_identification
    from src.causal.estimator import _apply_corrected_edges

    df = _scm_df(np, pd)  # z is a column even though the spec omits it
    spec = _spec("x", "y", _OMITTED[0], _OMITTED[1])
    recon = reconcile_graph(spec, df)

    assert recon is not None and recon.verdict == "corrected"
    assert any(c.kind == "add" and "z" in (c.source, c.target) for c in recon.changes)
    # The discovered confounder becomes an adjustment variable in identification.
    ident, _ = run_identification(_apply_corrected_edges(spec, recon.corrected_edges), df)
    assert ident.identifiable and "z" in ident.adjustment_set


def test_refuting_the_only_edge_is_applied_not_discarded():
    """Regression: an empty corrected_edges means the data refuted *every*
    asserted edge — the strongest signal discovery can give — not that nothing
    was found. The estimator used to guard on `recon.corrected_edges`, so this
    correction was silently dropped and identification then ran on the DAG the
    data had just contradicted (while the UI still showed the 'remove')."""
    pytest.importorskip("causallearn")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    from src.causal.estimator import _apply_corrected_edges, _should_apply_correction

    # x and y independent by construction; non-Gaussian so DirectLiNGAM works.
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"x": rng.normal(size=300) ** 3, "y": rng.normal(size=300) ** 3})
    spec = _spec("x", "y", (("x", "treatment"), ("y", "outcome")), (("x", "y"),))

    recon = reconcile_graph(spec, df)
    assert recon is not None and recon.verdict == "corrected"
    assert [(c.kind, c.source, c.target) for c in recon.changes] == [("remove", "x", "y")]
    assert recon.corrected_edges == []           # nothing survives — the bug's trigger

    # The estimator's own decision function must admit this case. Asserting on
    # _should_apply_correction (not a restated expression) is what makes this a
    # regression test: reverting the guard to `recon.corrected_edges` fails here.
    assert _should_apply_correction(recon) is True

    # And applying an empty edge set is safe (no variables to materialize).
    corrected_spec = _apply_corrected_edges(spec, recon.corrected_edges)
    assert corrected_spec.edges == []
    assert {v.id for v in corrected_spec.variables} == {"x", "y"}


def test_reconcile_never_raises_on_degenerate_data():
    pytest.importorskip("causallearn")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")
    # Constant columns / NaNs must degrade to None or a verdict, never raise.
    df = pd.DataFrame({"x": [1.0] * 60, "y": [2.0] * 60, "z": np.nan})
    spec = _spec("x", "y", _CORRECT[0], _CORRECT[1])
    out = reconcile_graph(spec, df)
    assert out is None or isinstance(out, GraphReconciliation)
