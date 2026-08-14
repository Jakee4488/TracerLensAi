"""Node-level instrumentation: recording, capping, and numeric extraction.

Hermetic — `node_trace`, `numeric` and `models` have no ADK/Vertex imports, so
none of this needs credentials or a network.
"""

from __future__ import annotations

import pytest

from src.causal import node_trace
from src.causal import state_keys as sk
from src.causal.models import NodeTrace
from src.causal.numeric import (
    ParsedNumber,
    extract_labelled,
    extract_numbers,
    find_match,
    strip_non_prose,
    within_tolerance,
)


# ── NodeTrace model ──────────────────────────────────────────────────────────

def test_values_drop_non_finite_and_uncoercible():
    """A NaN would pass or fail an arithmetic check depending only on which
    side of the comparison it landed on, so it must never be stored."""
    trace = NodeTrace(
        seq=1, node_id="n", node_kind="step",
        values={"good": 2.5, "int": 3, "nan": float("nan"),
                "inf": float("inf"), "text": "abc", "none": None},
    )
    assert trace.values == {"good": 2.5, "int": 3.0}


def test_inputs_outputs_are_json_safe_and_bounded():
    trace = NodeTrace(
        seq=1, node_id="n", node_kind="step",
        inputs={"long": "x" * 500, "nested": {"a": [1, 2, 3]}, "obj": object()},
        outputs={"ids": list(range(50))},
    )
    assert len(trace.inputs["long"]) == 300
    assert trace.inputs["nested"] == {"a": [1, 2, 3]}
    assert isinstance(trace.inputs["obj"], str)
    assert len(trace.outputs["ids"]) == 20  # capped
    # Must survive a JSON round trip: it is persisted in session state.
    import json
    json.loads(json.dumps(trace.model_dump(mode="json")))


# ── Recording ────────────────────────────────────────────────────────────────

def test_record_appends_and_increments_seq():
    state: dict = {}
    node_trace.record(state, node_id="a", node_kind="step", stage="s")
    node_trace.record(state, node_id="b", node_kind="step", stage="s")
    traces = state[sk.KEY_NODE_TRACES]
    assert [t["node_id"] for t in traces] == ["a", "b"]
    assert [t["seq"] for t in traces] == [1, 2]


def test_record_into_sink_leaves_state_untouched():
    """The controller batches writes into a state_delta rather than mutating
    session state directly; the delta is what ADK persists."""
    state: dict = {}
    delta: dict = {}
    node_trace.record(state, node_id="a", node_kind="step", stage="s", sink=delta)
    assert sk.KEY_NODE_TRACES not in state
    assert [t["node_id"] for t in delta[sk.KEY_NODE_TRACES]] == ["a"]


def test_chained_records_in_one_delta_accumulate():
    """Regression: reading only from `state` made every record in a batch start
    from the same pre-batch list, so each clobbered the last and a stage
    recording several nodes (the estimator) persisted only its final one."""
    state: dict = {}
    delta: dict = {}
    for node_id in ("identification", "effect", "counterfactual"):
        node_trace.record(state, node_id=node_id, node_kind="step", stage="e", sink=delta)
    assert [t["node_id"] for t in delta[sk.KEY_NODE_TRACES]] == [
        "identification", "effect", "counterfactual",
    ]
    assert [t["seq"] for t in delta[sk.KEY_NODE_TRACES]] == [1, 2, 3]


def test_chained_records_continue_existing_state():
    state = {sk.KEY_NODE_TRACES: [{"seq": 1, "node_id": "graph", "values": {}}]}
    delta: dict = {}
    node_trace.record(state, node_id="identification", node_kind="step", stage="e", sink=delta)
    node_trace.record(state, node_id="effect", node_kind="step", stage="e", sink=delta)
    assert [t["node_id"] for t in delta[sk.KEY_NODE_TRACES]] == [
        "graph", "identification", "effect",
    ]
    assert [t["seq"] for t in delta[sk.KEY_NODE_TRACES]] == [1, 2, 3]


def test_cap_evicts_oldest_and_counts_the_loss():
    """A capped log that silently drops its head is an audit trail that lies by
    omission — same contract as the change ledger."""
    state: dict = {}
    for i in range(5):
        node_trace.record(state, node_id=f"n{i}", node_kind="step", stage="s", cap=3)
    assert [t["node_id"] for t in state[sk.KEY_NODE_TRACES]] == ["n2", "n3", "n4"]
    assert state[sk.KEY_NODE_TRACES_DROPPED] == 2


def test_read_helpers():
    state: dict = {}
    node_trace.record(state, node_id="a", node_kind="step", stage="s", values={"x": 1.0})
    node_trace.record(state, node_id="effect", node_kind="effect", stage="s",
                      values={"point": 2.0, "n_obs": 60})
    traces = state[sk.KEY_NODE_TRACES]
    assert node_trace.visited_node_ids(traces) == ["a", "effect"]
    assert node_trace.values_index(traces) == {
        "a.x": 1.0, "effect.point": 2.0, "effect.n_obs": 60.0,
    }
    assert len(node_trace.traces_of_kind(traces, "effect")) == 1


def test_visited_preserves_repeat_visits():
    """A component executed twice after a replan is two visits; collapsing them
    would hide the retry."""
    state: dict = {}
    node_trace.record(state, node_id="a", node_kind="step", stage="s")
    node_trace.record(state, node_id="a", node_kind="step", stage="s")
    assert node_trace.visited_node_ids(state[sk.KEY_NODE_TRACES]) == ["a", "a"]


def test_values_index_later_entry_wins():
    state: dict = {}
    node_trace.record(state, node_id="a", node_kind="step", stage="s", values={"x": 1.0})
    node_trace.record(state, node_id="a", node_kind="step", stage="s", values={"x": 9.0})
    assert node_trace.values_index(state[sk.KEY_NODE_TRACES])["a.x"] == 9.0


# ── Numeric extraction ───────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("the effect is 2.0", [2.0]),
    ("about -1.5 units", [-1.5]),
    ("+3 higher", [3.0]),
    ("1,234.5 total", [1234.5]),
    ("p = 2.5e-3", [0.0025]),
    (".75 of the way", [0.75]),
    ("ATE 2.03 (95% CI 1.81 to 2.25)", [2.03, 95.0, 1.81, 2.25]),
    ("no numbers here", []),
])
def test_extract_numbers(text, expected):
    assert [n.value for n in extract_numbers(text)] == expected


def test_confidence_level_is_extracted_as_a_number_too():
    """Documented consequence of "does any number match" semantics: the 95 in
    "95% CI" joins the candidate pool. Harmless for the ground truths this repo
    checks (2.0, -1.5, 3.0, 0.139), but a case whose expected value sat near 95
    could pass on the confidence level — use `extract_labelled` there."""
    values = [n.value for n in extract_numbers("effect 2.0 (95% CI 1.8 to 2.2)")]
    assert 95.0 in values
    near = extract_labelled("effect 2.0 (95% CI 1.8 to 2.2)", ["effect"], window=4)
    assert [n.value for n in near] == [2.0]


def test_percent_is_flagged_and_convertible():
    found = extract_numbers("demand rose 15%")
    assert found[0].value == 15.0
    assert found[0].is_percent
    assert found[0].as_fraction == 0.15


def test_step_ids_do_not_parse_as_numbers():
    """Plan step ids like s6.r1 appear in traces; misreading them as values
    would inject noise into every check."""
    assert [n.value for n in extract_numbers("step s6.r1 done")] == []


def test_attached_files_and_fences_are_excluded():
    """Numbers in an attached CSV are the fixture, not anything the agent
    computed — a check must not be satisfiable by an echo of its own input."""
    text = (
        "--- Attached file: study.csv ---\n"
        "z,t,y\n0.305,-1.500,-3.055\n"
        "--- End of file: study.csv ---\n"
        "The estimated effect is 2.0."
    )
    assert [n.value for n in extract_numbers(text)] == [2.0]

    fenced = "```python\nx = 42\n```\nThe answer is 7."
    assert [n.value for n in extract_numbers(fenced)] == [7.0]


def test_strip_non_prose_preserves_offsets():
    text = "```py\nx=1\n```tail"
    assert len(strip_non_prose(text)) == len(text)


# ── Tolerance ────────────────────────────────────────────────────────────────

def test_tolerance_is_a_union_of_both_bounds():
    # Relative alone would fail; absolute rescues it.
    assert within_tolerance(2.10, 2.0, rel=0.01, abs_=0.2)
    # Absolute alone would fail; relative rescues it.
    assert within_tolerance(110.0, 100.0, rel=0.2, abs_=1.0)
    assert not within_tolerance(3.0, 2.0, rel=0.1, abs_=0.1)


def test_relative_tolerance_is_useless_at_zero_so_absolute_carries_it():
    assert not within_tolerance(0.05, 0.0, rel=0.5)
    assert within_tolerance(0.05, 0.0, abs_=0.1)


def test_non_finite_never_passes():
    assert not within_tolerance(float("nan"), 2.0, abs_=100.0)
    assert not within_tolerance(2.0, float("inf"), abs_=100.0)


# ── Matching ─────────────────────────────────────────────────────────────────

def test_find_match_accepts_a_number_anywhere_in_the_answer():
    text = "Adjusting for z, the causal effect of t on y is about 1.97 (95% CI 1.6-2.3)."
    match = find_match(text, 2.0, rel=0.15, abs_=0.25)
    assert match.passed and match.found == 1.97


def test_find_match_reports_the_closest_candidate_on_failure():
    """A failure must say what the agent actually claimed, not merely that it
    was wrong — otherwise every failure needs a manual trace read."""
    match = find_match("the effect is 0.5", 2.0, rel=0.1, abs_=0.1)
    assert not match.passed
    assert match.found == 0.5
    assert match.error == pytest.approx(1.5)


def test_find_match_sign_matters_by_default():
    assert not find_match("demand falls by 1.5", -1.5, rel=0.1, abs_=0.1).passed


def test_match_abs_accepts_direction_stated_in_words():
    match = find_match("demand falls by 1.5 units", -1.5, rel=0.1, abs_=0.1, match_abs=True)
    assert match.passed and match.used_abs


def test_find_match_with_no_numbers_present():
    match = find_match("no figures at all", 2.0, abs_=0.1)
    assert not match.passed and match.found is None


def test_find_match_is_deterministic_across_repeats():
    """Explicitly pinned: the whole eval layer is required to be deterministic,
    and an earlier draft iterated a set of candidate readings."""
    text = "values 1.9 and 2.1 and 2.0 appear"
    results = {(find_match(text, 2.0, rel=0.5, abs_=0.5).found) for _ in range(25)}
    assert len(results) == 1


def test_extract_labelled_targets_a_named_quantity():
    text = "The sample size was 60. The ATE is 2.04 with a p-value of 0.01."
    near = extract_labelled(text, ["ATE"], window=20)
    assert 2.04 in [n.value for n in near]
    assert 60.0 not in [n.value for n in near]


def test_parsed_number_fraction_is_identity_for_plain_numbers():
    assert ParsedNumber(value=2.0, start=0, end=1, raw="2.0").as_fraction == 2.0
