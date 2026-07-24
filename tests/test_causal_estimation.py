"""Tests for the DoWhy identification/estimation stage.

Split in two: the pure helpers (query gating, graph construction, dataset
parsing) run with no heavy deps; the identification/estimation correctness tests
`importorskip("dowhy")` so the core suite stays fast and hermetic when DoWhy
isn't installed.
"""

import pytest

from src.causal.complexity import is_effect_query
from src.causal.estimation import _acyclic_edges, build_causal_graph, run_identification
from src.causal.models import CausalEstimand, CausalVariable, VarEdge


def _spec(treatment, outcome, variables, edges):
    return CausalEstimand(
        treatment=treatment,
        outcome=outcome,
        variables=[CausalVariable(id=v, role=r) for v, r in variables],
        edges=[VarEdge(source=s, target=t) for s, t in edges],
    )


# ── Pure: effect-query gate ──────────────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "What is the effect of price on weekly demand?",
    "How much does advertising affect sales?",
    "Estimate the treatment effect of the drug on recovery.",
    "What is the price elasticity of demand?",
    "Estimate the impact of the minimum wage on employment, controlling for region.",
])
def test_is_effect_query_true(query):
    assert is_effect_query(query)


@pytest.mark.parametrize("query", [
    "Why is revenue down this quarter?",
    "Compare Postgres and MySQL for our workload.",
    "Summarize this document.",
    "What are the main drivers of churn?",  # causal wording, but not an effect-of ask
])
def test_is_effect_query_false(query):
    assert not is_effect_query(query)


def test_is_effect_query_ignores_attached_file_block():
    q = ("--- Attached file: notes.txt ---\nthe effect of X on Y\n--- End of file: notes.txt ---\n"
         "Please summarize the attached notes.")
    # The trigger phrase is only inside the attachment, so this is NOT an effect query.
    assert not is_effect_query(q)


# ── Pure: graph construction ─────────────────────────────────────────────────

def test_acyclic_edges_drops_cycle_and_self_loop():
    kept = _acyclic_edges([("a", "b"), ("b", "a"), ("c", "c"), ("b", "c")])
    assert kept == [("a", "b"), ("b", "c")]  # b->a would cycle; c->c is a self-loop


def test_build_causal_graph_includes_treatment_outcome_and_edges():
    spec = _spec("t", "y",
                 [("z", "confounder"), ("t", "treatment"), ("y", "outcome")],
                 [("z", "t"), ("z", "y"), ("t", "y")])
    gml, nodes, edges = build_causal_graph(spec)
    assert set(nodes) == {"z", "t", "y"}
    assert set(edges) == {("z", "t"), ("z", "y"), ("t", "y")}
    assert "graph" in gml and "directed 1" in gml


def test_build_causal_graph_adds_missing_treatment_outcome_nodes():
    spec = _spec("t", "y", [("z", "confounder")], [("z", "t"), ("z", "y")])
    _gml, nodes, _edges = build_causal_graph(spec)
    assert {"t", "y", "z"} <= set(nodes)


# ── Pure: no-DoWhy degradation ───────────────────────────────────────────────

def test_run_identification_rejects_same_treatment_and_outcome():
    # Returns before importing dowhy, so this is hermetic.
    ident, effect = run_identification(_spec("x", "x", [("x", "treatment")], []))
    assert not ident.identifiable
    assert ident.estimand_type == "none"
    assert effect is None


# ── Pure (pandas only): dataset extraction ───────────────────────────────────

def test_parse_dataset_from_attachment_block():
    pytest.importorskip("pandas")
    from src.causal.estimation import parse_dataset

    msg = ("--- Attached file: data.csv ---\n"
           "price,demand,season\n1,10,0\n2,9,1\n3,7,0\n4,6,1\n"
           "--- End of file: data.csv ---\n"
           "What is the effect of price on demand?")
    df = parse_dataset(msg)
    assert df is not None
    assert list(df.columns) == ["price", "demand", "season"]
    assert df.shape[0] == 4


def test_parse_dataset_none_when_no_table():
    pytest.importorskip("pandas")
    from src.causal.estimation import parse_dataset
    assert parse_dataset("Just a plain question with no data.") is None


# ── DoWhy: identification correctness (data-free) ─────────────────────────────

def test_identifies_confounder_adjustment_set():
    pytest.importorskip("dowhy")
    spec = _spec("t", "y",
                 [("z", "confounder"), ("t", "treatment"), ("y", "outcome")],
                 [("z", "t"), ("z", "y"), ("t", "y")])
    ident, effect = run_identification(spec)  # no data -> identification only
    assert ident.identifiable
    assert ident.estimand_type == "backdoor"
    assert "z" in ident.adjustment_set
    assert effect is None


def test_mediator_is_excluded_from_adjustment_set():
    pytest.importorskip("dowhy")
    # z confounds t and y; m is a mediator on t -> m -> y. Adjusting for m would
    # block part of the causal effect, so DoWhy must NOT include it.
    spec = _spec("t", "y",
                 [("z", "confounder"), ("m", "mediator"),
                  ("t", "treatment"), ("y", "outcome")],
                 [("z", "t"), ("z", "y"), ("t", "m"), ("m", "y"), ("t", "y")])
    ident, _ = run_identification(spec)
    assert ident.identifiable
    assert "z" in ident.adjustment_set
    assert "m" not in ident.adjustment_set


def test_collider_is_excluded_from_adjustment_set():
    pytest.importorskip("dowhy")
    # c is a collider (t -> c <- y). Conditioning on it opens a spurious path.
    spec = _spec("t", "y",
                 [("z", "confounder"), ("c", "other"),
                  ("t", "treatment"), ("y", "outcome")],
                 [("z", "t"), ("z", "y"), ("t", "y"), ("t", "c"), ("y", "c")])
    ident, _ = run_identification(spec)
    assert "z" in ident.adjustment_set
    assert "c" not in ident.adjustment_set


# ── DoWhy: estimation recovers a known effect ────────────────────────────────

def test_estimation_recovers_known_effect_and_refutes():
    pytest.importorskip("dowhy")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    rng = np.random.default_rng(0)
    n = 2000
    z = rng.normal(size=n)
    t = 0.5 * z + rng.normal(size=n)
    y = 2.0 * t + 1.5 * z + rng.normal(size=n)  # true effect of t on y is 2.0
    df = pd.DataFrame({"z": z, "t": t, "y": y})

    spec = _spec("t", "y",
                 [("z", "confounder"), ("t", "treatment"), ("y", "outcome")],
                 [("z", "t"), ("z", "y"), ("t", "y")])
    ident, effect = run_identification(spec, df)

    assert ident.identifiable and "z" in ident.adjustment_set
    assert effect is not None and effect.method
    assert effect.n_obs == n
    assert abs(effect.point - 2.0) < 0.3  # backdoor adjustment recovers the truth
    assert effect.refutations  # robustness checks ran
    for r in effect.refutations:
        assert np.isfinite(r.new_effect)
