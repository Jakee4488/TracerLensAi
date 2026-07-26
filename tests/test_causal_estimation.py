"""Tests for the DoWhy identification/estimation stage.

Split in two: the pure helpers (query gating, graph construction, dataset
parsing) run with no heavy deps; the identification/estimation correctness tests
`importorskip("dowhy")` so the core suite stays fast and hermetic when DoWhy
isn't installed.
"""

import pytest

from src.causal.complexity import is_counterfactual_query, is_effect_query
from src.causal.estimation import (
    _acyclic_edges,
    _method_for,
    acquire_dataframe,
    build_causal_graph,
    dataset_headers,
    parse_web_retrieval,
    run_counterfactual,
    run_identification,
)
from src.causal.models import CausalEstimand, CausalVariable, IdentificationResult, VarEdge


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


# ── Pure: counterfactual-query gate ──────────────────────────────────────────

@pytest.mark.parametrize("query", [
    "What would demand have been had we not raised the price?",
    "If trade winds had stayed constant, what would SST be?",
    "Give me the counterfactual outcome for store 12.",
    "What would sales be if we had used campaign A instead of B?",
])
def test_is_counterfactual_query_true(query):
    assert is_counterfactual_query(query)


@pytest.mark.parametrize("query", [
    "What is the effect of price on demand?",
    "Why is revenue down?",
    "Forecast next quarter's sales.",
])
def test_is_counterfactual_query_false(query):
    assert not is_counterfactual_query(query)


# ── Pure: header sniffing (no pandas) ────────────────────────────────────────

def test_dataset_headers_from_attachment_block():
    msg = ("--- Attached file: data.csv ---\n"
           "Price,Weekly Demand,season\n1,10,0\n2,9,1\n"
           "--- End of file: data.csv ---\nWhat is the effect of price on demand?")
    assert dataset_headers(msg) == ["price", "weekly_demand", "season"]


def test_dataset_headers_from_fenced_block_tsv():
    msg = "```\nprice\tdemand\n1\t10\n2\t9\n```"
    assert dataset_headers(msg) == ["price", "demand"]


def test_dataset_headers_none_without_table():
    assert dataset_headers("no data here, just a question") == []


# ── Pure: estimator method selection ─────────────────────────────────────────

def test_method_for_matches_estimand_type():
    assert _method_for(IdentificationResult(estimand_type="backdoor")) == "backdoor.linear_regression"
    assert _method_for(IdentificationResult(estimand_type="iv", instruments=["z"])) == "iv.instrumental_variable"
    assert _method_for(IdentificationResult(estimand_type="frontdoor")) == "frontdoor.two_stage_regression"
    # IV identified but no instrument extracted -> fall back to backdoor.
    assert _method_for(IdentificationResult(estimand_type="iv")) == "backdoor.linear_regression"


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


# ── Pure: web-search output parsing + data acquisition ───────────────────────

def test_parse_web_retrieval_dataset():
    pytest.importorskip("pandas")
    text = ("Here is what I found.\n```csv\nprice,demand\n1,10\n2,9\n3,7\n4,6\n```\n"
            "SOURCES: https://example.org/data\nWEB_STATUS: dataset")
    web, csv_text = parse_web_retrieval(text)
    assert web.mode == "dataset" and web.row_count == 4
    assert "https://example.org/data" in web.sources
    assert csv_text is not None and "price,demand" in csv_text


def test_parse_web_retrieval_evidence():
    web, csv_text = parse_web_retrieval(
        "EVIDENCE: income confounds price and demand\n"
        "EVIDENCE: reported elasticity is about -1.2\n"
        "SOURCES: https://example.org/a\nWEB_STATUS: evidence")
    assert web.mode == "evidence" and csv_text is None
    assert len(web.evidence) == 2
    assert web.sources == ["https://example.org/a"]


def test_parse_web_retrieval_none():
    web, csv_text = parse_web_retrieval("I couldn't find anything useful.\nWEB_STATUS: none")
    assert web.mode == "none" and csv_text is None


def test_acquire_dataframe_prefers_attachment_then_web():
    pytest.importorskip("pandas")
    attached = ("--- Attached file: d.csv ---\na,b\n1,2\n3,4\n5,6\n--- End of file: d.csv ---")
    web_csv = "```csv\na,b\n9,9\n8,8\n7,7\n```"
    # Attachment wins when present.
    df = acquire_dataframe(attached, web_csv)
    assert df is not None and int(df["a"].iloc[0]) == 1
    # Falls back to the web CSV when the message has no table.
    df2 = acquire_dataframe("no data here", web_csv)
    assert df2 is not None and int(df2["a"].iloc[0]) == 9
    # None when neither yields a table.
    assert acquire_dataframe("no data here", None) is None


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
        if r.p_value is not None:  # p-value drives the verdict when present
            assert 0.0 <= r.p_value <= 1.0


# ── DoWhy gcm: counterfactuals (rung 3) ──────────────────────────────────────

def _linear_scm_df(np, pd, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    z = rng.normal(size=n)
    t = 0.5 * z + rng.normal(size=n)
    y = 2.0 * t + 1.5 * z + rng.normal(size=n)  # true effect of t on y is 2.0
    return pd.DataFrame({"z": z, "t": t, "y": y})


def test_counterfactual_matches_linear_ate():
    pytest.importorskip("dowhy")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    spec = _spec("t", "y",
                 [("z", "confounder"), ("t", "treatment"), ("y", "outcome")],
                 [("z", "t"), ("z", "y"), ("t", "y")])
    spec.baseline_value = 0.0
    spec.intervention_value = 1.0
    cf = run_counterfactual(spec, _linear_scm_df(np, pd))

    assert cf is not None
    assert cf.baseline_value == 0.0 and cf.intervention_value == 1.0
    # In a linear SCM, delta under do(t: 0 -> 1) equals the ATE (2.0).
    assert abs(cf.delta - 2.0) < 0.4


def test_counterfactual_defaults_to_quartiles():
    pytest.importorskip("dowhy")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    spec = _spec("t", "y",
                 [("z", "confounder"), ("t", "treatment"), ("y", "outcome")],
                 [("z", "t"), ("z", "y"), ("t", "y")])
    df = _linear_scm_df(np, pd)
    cf = run_counterfactual(spec, df)

    assert cf is not None
    assert cf.baseline_value == pytest.approx(float(df["t"].quantile(0.25)))
    assert cf.intervention_value == pytest.approx(float(df["t"].quantile(0.75)))
    expected = 2.0 * (cf.intervention_value - cf.baseline_value)
    assert abs(cf.delta - expected) < 0.5


def test_counterfactual_requires_data():
    spec = _spec("t", "y", [("t", "treatment"), ("y", "outcome")], [("t", "y")])
    assert run_counterfactual(spec, None) is None
