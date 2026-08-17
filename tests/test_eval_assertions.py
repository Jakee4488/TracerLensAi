"""Deterministic eval checks: arithmetic accuracy and causal node-path shape.

`tests/eval/` is not a package (it holds datasets and CLI-loaded metric files,
not tests), so the module under test is loaded by path.

These tests double as the worked example of how node-level logs are used to
assert that the agent's reasoning passed through the expected causal nodes —
see the `test_node_path_*` block and `test_worked_example_*` at the bottom.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_EVAL_DIR = Path(__file__).resolve().parent / "eval"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_tl_{name}", _EVAL_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


assertions = _load("assertions")


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_nodes_block(nodes: list[dict]) -> str:
    return "```causal-nodes\n" + json.dumps({"nodes": nodes, "dropped": 0}) + "\n```"


def make_instance(*, prompt: str = "", response: str = "",
                  nodes: list[dict] | None = None,
                  block_in_response: bool = False) -> dict:
    """A grading instance shaped like the one agents-cli passes to a metric."""
    texts = [response]
    if nodes is not None and not block_in_response:
        texts.append(make_nodes_block(nodes))
    if nodes is not None and block_in_response:
        texts = [response + "\n" + make_nodes_block(nodes)]
    agent_data = {"turns": [{"turn_index": 0, "events": [
        {"author": "CausalSynthesizer", "content": {"role": "model",
                                                    "parts": [{"text": t}]}}
        for t in texts
    ]}]}
    return {"prompt": prompt, "response": response,
            "agent_data": json.dumps(agent_data)}


def node(node_id: str, kind: str, *, seq: int = 1,
         values: dict | None = None, outputs: dict | None = None) -> dict:
    return {"seq": seq, "node_id": node_id, "node_kind": kind,
            "values": values or {}, "outputs": outputs or {}}


IDENT = node("identification", "identification", seq=1,
             values={"identifiable": 1.0, "adjustment_set_size": 1},
             outputs={"identifiable": True, "estimand_type": "backdoor",
                      "adjustment_set": ["z"], "treatment": "t", "outcome": "y"})
EFFECT = node("effect", "effect", seq=2, values={"point": 1.98, "n_obs": 60},
              outputs={"point": 1.98})


# ── Node-trace extraction ────────────────────────────────────────────────────

def test_extract_node_traces_from_trace_events():
    instance = make_instance(nodes=[IDENT, EFFECT])
    found = assertions.extract_node_traces(instance)
    assert [n["node_id"] for n in found] == ["identification", "effect"]


def test_extract_node_traces_from_the_response_field():
    """Whether the emitter's block ends up in the trace or folded into the
    graded response depends on how the response is assembled, so both are
    scanned and the check does not depend on that detail."""
    instance = make_instance(response="answer", nodes=[IDENT], block_in_response=True)
    assert len(assertions.extract_node_traces(instance)) == 1


def test_duplicate_blocks_are_deduplicated():
    block = make_nodes_block([IDENT])
    instance = {"prompt": "", "response": block,
                "agent_data": json.dumps({"turns": [{"events": [
                    {"author": "a", "content": {"parts": [{"text": block}]}}]}]})}
    assert len(assertions.extract_node_traces(instance)) == 1


def test_missing_or_malformed_blocks_yield_nothing():
    assert assertions.extract_node_traces({"response": "plain answer"}) == []
    assert assertions.extract_node_traces(
        {"response": "```causal-nodes\n{not json}\n```"}) == []
    assert assertions.extract_node_traces({}) == []


def test_agent_data_as_dict_is_accepted():
    """Real runs pass a JSON string; tests and older CLI versions pass a dict."""
    instance = {"prompt": "", "response": "",
                "agent_data": {"turns": [{"events": [
                    {"content": {"parts": [{"text": make_nodes_block([IDENT])}]}}]}]}}
    assert len(assertions.extract_node_traces(instance)) == 1


def test_node_values_are_flattened_and_addressable():
    values = assertions.node_values([IDENT, EFFECT])
    assert values["effect.point"] == 1.98
    assert values["identification.adjustment_set_size"] == 1.0


# ── Tolerance resolution (per-metric configurability) ────────────────────────

def test_tolerance_precedence_check_over_metric_over_fallback():
    expectations = {"tolerance_defaults": {"numeric_accuracy": {"rel": 0.3, "abs": 0.9}}}
    # 1. the check's own tolerance wins
    assert assertions.resolve_tolerance(
        {"tolerance": {"rel": 0.01}}, "numeric_accuracy", expectations)["rel"] == 0.01
    # 2. else the metric default
    assert assertions.resolve_tolerance({}, "numeric_accuracy", expectations)["rel"] == 0.3
    # 3. else the global fallback
    assert assertions.resolve_tolerance({}, "other_metric", expectations)["rel"] == 0.20


def test_partial_check_tolerance_merges_rather_than_replaces():
    """Overriding only `rel` must keep the metric's `abs`, or a narrow override
    silently widens the other bound."""
    expectations = {"tolerance_defaults": {"numeric_accuracy": {"rel": 0.3, "abs": 0.9}}}
    resolved = assertions.resolve_tolerance(
        {"tolerance": {"rel": 0.05}}, "numeric_accuracy", expectations)
    assert resolved == {"rel": 0.05, "abs": 0.9}


# ── Numeric checks ───────────────────────────────────────────────────────────

def test_numeric_check_against_a_node_value_passes():
    case = {"numeric": [{"name": "ate", "expected": 2.0, "source": "node:effect.point",
                         "tolerance": {"rel": 0.1, "abs": 0.1}}]}
    results = assertions.run_numeric_checks(
        make_instance(nodes=[IDENT, EFFECT]), case, {})
    assert results[0]["passed"] and results[0]["found"] == 1.98


def test_numeric_check_against_a_node_value_fails_out_of_tolerance():
    wrong = node("effect", "effect", seq=2, values={"point": 3.7})
    case = {"numeric": [{"name": "ate", "expected": 2.0, "source": "node:effect.point",
                         "tolerance": {"rel": 0.1, "abs": 0.1}}]}
    results = assertions.run_numeric_checks(make_instance(nodes=[wrong]), case, {})
    assert not results[0]["passed"]
    assert "3.7" in results[0]["detail"]


def test_missing_node_value_names_what_was_available():
    case = {"numeric": [{"name": "cf", "expected": 3.0,
                         "source": "node:counterfactual.delta"}]}
    results = assertions.run_numeric_checks(make_instance(nodes=[EFFECT]), case, {})
    assert not results[0]["passed"]
    assert "effect.point" in results[0]["detail"]


def test_node_check_without_traces_fails_loudly():
    """A silent skip would be indistinguishable from a pass, which is the one
    failure mode that makes a check worse than not having it."""
    case = {"numeric": [{"name": "ate", "expected": 2.0, "source": "node:effect.point"}]}
    results = assertions.run_numeric_checks(
        {"prompt": "", "response": "the effect is 2.0"}, case, {})
    assert not results[0]["passed"]
    assert results[0]["skipped_reason"] == "no_node_traces"
    assert "CAUSAL_NODE_TRACE=1" in results[0]["detail"]


def test_numeric_check_against_the_answer_prose():
    case = {"numeric": [{"name": "ate", "expected": 2.0, "source": "answer",
                         "tolerance": {"rel": 0.15, "abs": 0.2}}]}
    instance = make_instance(response="Adjusting for z, the effect is about 1.95.")
    assert assertions.run_numeric_checks(instance, case, {})[0]["passed"]


def test_answer_check_ignores_numbers_from_the_attached_csv():
    """Otherwise a case could pass on an echo of its own input data."""
    case = {"numeric": [{"name": "ate", "expected": 2.0, "source": "answer",
                         "tolerance": {"rel": 0.05, "abs": 0.05}}]}
    instance = make_instance(response=(
        "--- Attached file: d.csv ---\nz,t,y\n2.0,1,1\n--- End of file: d.csv ---\n"
        "I could not compute an estimate."))
    assert not assertions.run_numeric_checks(instance, case, {})[0]["passed"]


def test_match_abs_lets_a_worded_direction_pass():
    case = {"numeric": [{"name": "slope", "expected": -1.5, "source": "answer",
                         "match_abs": True, "tolerance": {"rel": 0.2, "abs": 0.3}}]}
    instance = make_instance(response="A unit price rise reduces demand by about 1.5 units.")
    assert assertions.run_numeric_checks(instance, case, {})[0]["passed"]


def test_expectation_without_a_value_is_a_failure_not_a_crash():
    results = assertions.run_numeric_checks(
        make_instance(), {"numeric": [{"name": "x"}]}, {})
    assert not results[0]["passed"]


# ── Node-path checks (the worked example) ────────────────────────────────────

def test_node_path_requires_expected_kinds_and_visits():
    case = {"nodes": {"require_kinds": ["identification", "effect"],
                      "expect_visited": ["identification", "effect"]}}
    results = assertions.run_node_checks(make_instance(nodes=[IDENT, EFFECT]), case)
    assert all(r["passed"] for r in results)


def test_node_path_detects_a_stage_that_never_ran():
    """The estimand stage is skip-gated; if the gate misfires, identification
    never happens and no prose judge reliably notices."""
    case = {"nodes": {"require_kinds": ["identification"],
                      "expect_visited": ["identification"]}}
    only_graph = node("graph", "graph", seq=1)
    results = assertions.run_node_checks(make_instance(nodes=[only_graph]), case)
    assert not any(r["passed"] for r in results)


def test_node_path_order_is_a_subsequence_not_equality():
    """Extra nodes between the required ones are legitimate — a replan retry,
    an extra plan step — but the required ones must appear in order."""
    nodes = [node("graph", "graph", seq=1), IDENT,
             node("s1", "step", seq=3), EFFECT]
    case = {"nodes": {"expect_order": ["graph", "identification", "effect"]}}
    assert assertions.run_node_checks(make_instance(nodes=nodes), case)[0]["passed"]

    reversed_case = {"nodes": {"expect_order": ["effect", "identification"]}}
    assert not assertions.run_node_checks(
        make_instance(nodes=nodes), reversed_case)[0]["passed"]


def test_forbidden_node_must_not_be_visited():
    case = {"nodes": {"forbid_visited": ["counterfactual"]}}
    assert assertions.run_node_checks(make_instance(nodes=[IDENT]), case)[0]["passed"]
    cf = node("counterfactual", "counterfactual", seq=2)
    assert not assertions.run_node_checks(
        make_instance(nodes=[IDENT, cf]), case)[0]["passed"]


def test_node_ids_match_tolerantly_across_llm_paraphrase():
    """Ids are slugified LLM output: `season` may arrive as `season_indicator`.
    Exact matching would fail on paraphrase rather than on substance."""
    ident = node("identification", "identification",
                 outputs={"adjustment_set": ["season_indicator"], "identifiable": True})
    case = {"nodes": {"adjustment_set_includes": ["season"]}}
    assert assertions.run_node_checks(make_instance(nodes=[ident]), case)[0]["passed"]


def test_estimand_type_and_identifiability_assertions():
    case = {"nodes": {"estimand_type": "backdoor", "require_identifiable": True}}
    assert all(r["passed"] for r in
               assertions.run_node_checks(make_instance(nodes=[IDENT]), case))

    iv = node("identification", "identification",
              outputs={"estimand_type": "iv", "identifiable": True, "adjustment_set": []})
    assert not assertions.run_node_checks(
        make_instance(nodes=[iv]), {"nodes": {"estimand_type": "backdoor"}})[0]["passed"]


def test_node_checks_without_traces_fail_loudly():
    results = assertions.run_node_checks(
        {"prompt": "", "response": "x"}, {"nodes": {"require_kinds": ["identification"]}})
    assert not results[0]["passed"]
    assert results[0]["skipped_reason"] == "no_node_traces"


# The two structural checks that motivated the whole node-trace mechanism.

def test_worked_example_mediator_must_not_enter_the_adjustment_set():
    """The money check. `skills` is a mediator on training -> skills -> earnings;
    adjusting for it blocks part of the total effect. This is asserted against
    the FORMAL estimand the identification node produced, not against prose —
    a model can write "we must not adjust for the mediator" while its estimand
    adjusts for it anyway, and only this check catches that."""
    case = {"nodes": {"adjustment_set_includes": ["family"],
                      "adjustment_set_excludes": ["skill"],
                      "require_identifiable": True}}

    correct = node("identification", "identification",
                   outputs={"adjustment_set": ["family_background"],
                            "identifiable": True, "estimand_type": "backdoor"})
    assert all(r["passed"] for r in
               assertions.run_node_checks(make_instance(nodes=[correct]), case))

    overadjusted = node("identification", "identification",
                        outputs={"adjustment_set": ["family_background", "skills"],
                                 "identifiable": True, "estimand_type": "backdoor"})
    results = assertions.run_node_checks(make_instance(nodes=[overadjusted]), case)
    failed = [r for r in results if not r["passed"]]
    assert [r["name"] for r in failed] == ["never-adjust-for:skill"]
    assert "skills" in failed[0]["detail"]


def test_worked_example_collider_must_not_be_conditioned_on():
    case = {"nodes": {"adjustment_set_excludes": ["hospital"]}}
    conditioned = node("identification", "identification",
                       outputs={"adjustment_set": ["hospitalization"],
                                "identifiable": True})
    assert not assertions.run_node_checks(
        make_instance(nodes=[conditioned]), case)[0]["passed"]


# ── Scoring and case resolution ──────────────────────────────────────────────

def test_score_is_the_fraction_of_checks_passed():
    scored = assertions.score_results(
        [{"name": "a", "passed": True}, {"name": "b", "passed": False}], "k")
    assert scored["score"] == 0.5
    assert "FAIL b" in scored["explanation"]


def test_no_expectations_scores_one_but_says_not_applicable():
    """1.0 here means 'nothing was checked', not 'checked and correct'."""
    scored = assertions.score_results([], "numeric_accuracy")
    assert scored["score"] == 1.0
    assert "not applicable" in scored["explanation"]


def test_resolve_case_id_matches_the_real_datasets():
    """Case identity comes from the prompt, because the grading instance does
    not carry eval_case_id."""
    prompt = ("[[causal:on]] What is the total effect of job training on earnings? "
              "Training raises skills, and skills raise earnings; family background "
              "affects both training uptake and earnings. Should the analysis "
              "control for skills?")
    assert assertions.resolve_case_id(prompt) == "mediator_must_not_be_adjusted"


def test_resolve_case_id_returns_none_for_an_unknown_prompt():
    assert assertions.resolve_case_id("something nobody put in a dataset") is None
    assert assertions.resolve_case_id("") is None


def test_evaluate_case_end_to_end_against_the_real_expectations_file():
    """Full path: prompt -> case id -> expectations.json -> node checks."""
    prompt = ("[[causal:on]] We study whether exercise affects heart disease. Both "
              "exercise and heart disease influence hospital admission. Should we "
              "restrict the analysis to hospitalized patients or control for "
              "hospitalization?")
    clean = node("identification", "identification",
                 outputs={"adjustment_set": [], "identifiable": True})
    result = assertions.evaluate_case(
        make_instance(prompt=prompt, nodes=[clean]), "causal_node_path")
    assert result["score"] == 1.0
    assert "collider_must_not_be_adjusted" in result["explanation"]

    bad = node("identification", "identification",
               outputs={"adjustment_set": ["hospitalization"], "identifiable": True})
    failing = assertions.evaluate_case(
        make_instance(prompt=prompt, nodes=[bad]), "causal_node_path")
    assert failing["score"] < 1.0
    assert "never-adjust-for:hospital" in failing["explanation"]


def test_evaluate_case_unknown_prompt_is_not_applicable():
    result = assertions.evaluate_case({"prompt": "unknown"}, "numeric_accuracy")
    assert result["score"] == 1.0
    assert "not applicable" in result["explanation"]


def test_shipped_expectations_file_is_valid():
    """Guards the config itself: a typo'd tolerance key or a case id that no
    dataset defines would otherwise only surface during a live eval run."""
    expectations = assertions.load_expectations()
    known = set(assertions._case_prompts())
    for case_id, case in (expectations.get("cases") or {}).items():
        assert case_id in known, f"expectations reference unknown case '{case_id}'"
        for check in case.get("numeric") or []:
            assert isinstance(check.get("expected"), (int, float))
            assert check.get("source", "answer") == "answer" or \
                check["source"].startswith("node:")
            for key in (check.get("tolerance") or {}):
                assert key in ("rel", "abs"), f"unknown tolerance key '{key}'"


# ── Trap datasets actually trap ──────────────────────────────────────────────
# The point of causal-traps-dataset.json is discrimination: a broken agent must
# FAIL and a correct agent must PASS. A case both answers survive is not a trap,
# it is saturation with extra steps. These tests are what stop a tolerance from
# being widened until a trap quietly stops trapping.

def _trap_cases():
    expectations = assertions.load_expectations()
    return [(cid, case) for cid, case in (expectations.get("cases") or {}).items()
            if "_trap" in case]


def _instance_reporting(case_id: str, value, expectations) -> dict:
    """A grading instance for an agent whose estimator produced `value`."""
    case = expectations["cases"][case_id]
    ident_out = {"identifiable": True, "estimand_type": "backdoor",
                 "adjustment_set": list(case.get("nodes", {}).get(
                     "adjustment_set_includes") or [])}
    nodes = [node("identification", "identification", seq=1,
                  values={"identifiable": 1.0,
                          "adjustment_set_size": len(ident_out["adjustment_set"])},
                  outputs=ident_out)]
    if value is not None:
        nodes.append(node("effect", "effect", seq=2, values={"point": float(value)},
                          outputs={"point": float(value)}))
    prompt = _prompt_for(case_id)
    return make_instance(prompt=prompt, response=f"The estimated effect is {value}.",
                         nodes=nodes)


def _prompt_for(case_id: str) -> str:
    for path in (Path(__file__).resolve().parent / "eval" / "datasets").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for case in data.get("eval_cases") or []:
            if case.get("eval_case_id") == case_id:
                return " ".join(p.get("text", "")
                                for p in (case.get("prompt") or {}).get("parts") or [])
    raise AssertionError(f"no dataset case for {case_id}")


def test_every_trap_case_has_both_numeric_and_structural_ground_truth():
    """The stated requirement: numeric truth where one exists, and structural
    truth (which variables belong in the adjustment set) for all of them."""
    for case_id, case in _trap_cases():
        nodes = case.get("nodes") or {}
        assert nodes, f"{case_id}: no structural expectations"
        structural = (nodes.get("adjustment_set_includes")
                      or nodes.get("adjustment_set_excludes")
                      or nodes.get("adjustment_set_empty") is not None
                      or nodes.get("require_identifiable") is False)
        assert structural, f"{case_id}: declares no adjustment-set ground truth"
        if case.get("_correct_estimate") is not None:
            assert case.get("numeric"), f"{case_id}: has a numeric truth but no check"


@pytest.mark.parametrize("case_id", [cid for cid, _ in _trap_cases()])
def test_correct_agent_passes_and_naive_agent_fails(case_id):
    """Both halves matter. Passing only the first would let a case that nothing
    can fail count as hardening."""
    expectations = assertions.load_expectations()
    case = expectations["cases"][case_id]
    correct, naive = case["_correct_estimate"], case["_naive_estimate"]

    if correct is None:  # non-identifiable case: structural only, no number
        pytest.skip("structural-only case, covered by the identifiability test")

    good = assertions.evaluate_case(
        _instance_reporting(case_id, correct, expectations), "numeric_accuracy")
    assert good["score"] == 1.0, f"correct estimate rejected:\n{good['explanation']}"

    bad = assertions.evaluate_case(
        _instance_reporting(case_id, naive, expectations), "numeric_accuracy")
    assert bad["score"] < 1.0, (
        f"NAIVE estimate {naive} passed — this case is not a trap:\n{bad['explanation']}")


def test_non_identifiable_case_rejects_an_agent_that_answers_anyway():
    """The spurious/unobserved-confounder case: an agent that reports a number
    has substituted a confounded regression for a causal claim."""
    case_id = "spurious_unobserved_confounder_csv"
    prompt = _prompt_for(case_id)

    honest = node("identification", "identification",
                  outputs={"identifiable": False, "estimand_type": "none",
                           "adjustment_set": []})
    result = assertions.evaluate_case(
        make_instance(prompt=prompt, nodes=[honest]), "causal_node_path")
    assert result["score"] == 1.0, result["explanation"]

    overconfident = node("identification", "identification",
                         outputs={"identifiable": True, "estimand_type": "backdoor",
                                  "adjustment_set": ["brand_demand"]})
    bad = assertions.evaluate_case(
        make_instance(prompt=prompt, nodes=[overconfident]), "causal_node_path")
    assert bad["score"] < 1.0
    assert "identifiable" in bad["explanation"]


@pytest.mark.parametrize("case_id,forbidden", [
    ("mediator_full_mediation_csv", "skill_score"),
    ("mediator_with_confounder_csv", "study_hours"),
    ("collider_null_effect_csv", "hospital_visit"),
    ("collider_with_confounder_csv", "clinic_record"),
])
def test_adjusting_for_the_trap_variable_fails_structurally(case_id, forbidden):
    """An agent that adjusts for everything is caught by the adjustment set
    itself, independently of whatever number it reported."""
    expectations = assertions.load_expectations()
    case = expectations["cases"][case_id]
    adjust = list((case.get("nodes") or {}).get("adjustment_set_includes") or [])

    ident = node("identification", "identification",
                 outputs={"identifiable": True, "estimand_type": "backdoor",
                          "adjustment_set": adjust + [forbidden]})
    result = assertions.evaluate_case(
        make_instance(prompt=_prompt_for(case_id), nodes=[ident]), "causal_node_path")
    assert result["score"] < 1.0
    assert f"never-adjust-for:{forbidden}" in result["explanation"]


def test_empty_adjustment_set_is_a_checkable_claim():
    """"Adjust for nothing" is the correct answer for full mediation, a pure
    collider, and reverse causation — an agent that adjusts for anything there
    has over-adjusted, and that must be catchable structurally."""
    case = {"nodes": {"adjustment_set_empty": True}}
    empty = node("identification", "identification",
                 outputs={"adjustment_set": [], "identifiable": True})
    assert assertions.run_node_checks(make_instance(nodes=[empty]), case)[0]["passed"]

    nonempty = node("identification", "identification",
                    outputs={"adjustment_set": ["skill_score"], "identifiable": True})
    result = assertions.run_node_checks(make_instance(nodes=[nonempty]), case)[0]
    assert not result["passed"] and "skill_score" in result["detail"]


def test_short_id_patterns_fall_back_to_exact_match():
    """Guard on the fuzzy matcher: a one-character pattern must not match every
    id containing that letter, or an exclusion check fails over a letter."""
    assert assertions._id_matches("season_indicator", "season")
    assert not assertions._id_matches("income", "m")
    assert assertions._id_matches("m", "m")


@pytest.mark.parametrize("metric_file", ["metric_numeric_accuracy", "metric_node_path"])
def test_cli_metric_entrypoints_expose_evaluate(metric_file):
    """custom_function_file requires exactly one module-level `evaluate`."""
    module = _load(metric_file)
    assert callable(module.evaluate)
    result = module.evaluate({"prompt": "unknown prompt", "response": ""})
    assert set(result) == {"score", "explanation"}
