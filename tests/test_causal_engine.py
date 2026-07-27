"""Unit tests for the deterministic causal graph engine (no ADK, no network)."""

import pytest

from src.causal.graph_engine import CausalTaskGraph
from src.causal.ledger import append_record, latest_for_step, next_seq
from src.causal.models import (
    CausalDecomposition,
    CausalEdge,
    ChangeRecord,
    ComponentDraft,
    NewStepDraft,
    ReplanRequest,
    ReplanResult,
)
from src.causal.state_keys import MAX_COMPONENTS, MAX_EDGES


def make_decomposition():
    """data -> features -> model -> forecast(outcome); constraint -> model."""
    return CausalDecomposition(
        goal="Forecast revenue impact of a 10% price increase",
        components=[
            ComponentDraft(id="data", label="Sales data", kind="input"),
            ComponentDraft(id="features", label="Feature prep", kind="process"),
            ComponentDraft(id="model", label="Elasticity model", kind="process"),
            ComponentDraft(id="budget", label="Compute budget", kind="constraint"),
            ComponentDraft(id="forecast", label="Revenue forecast", kind="outcome"),
        ],
        edges=[
            CausalEdge(source="data", target="features", confidence=0.9),
            CausalEdge(source="features", target="model", confidence=0.8),
            CausalEdge(source="model", target="forecast", confidence=0.9),
            CausalEdge(source="budget", target="model", relation="constrains", confidence=0.5),
        ],
    )


def test_build_graph_from_decomposition():
    graph = CausalTaskGraph.from_decomposition(make_decomposition())
    assert {c.id for c in graph.model.components} == {"data", "features", "model", "budget", "forecast"}
    assert len(graph.model.edges) == 4
    assert graph.model.goal.startswith("Forecast revenue")
    assert graph.model.repair_notes == []


def test_dangling_and_duplicate_edges_dropped():
    dec = make_decomposition()
    dec.edges.append(CausalEdge(source="ghost", target="model"))
    dec.edges.append(CausalEdge(source="data", target="features"))  # duplicate
    dec.edges.append(CausalEdge(source="model", target="model"))    # self-loop
    graph = CausalTaskGraph.from_decomposition(dec)
    assert len(graph.model.edges) == 4
    notes = " ".join(graph.model.repair_notes)
    assert "dangling" in notes and "duplicate" in notes and "self-loop" in notes


def test_cycle_repair_removes_lowest_confidence_edge():
    dec = make_decomposition()
    # forecast -> data closes a cycle; lowest confidence so it must be removed.
    dec.edges.append(CausalEdge(source="forecast", target="data", confidence=0.1))
    graph = CausalTaskGraph.from_decomposition(dec)
    pairs = {(e.source, e.target) for e in graph.model.edges}
    assert ("forecast", "data") not in pairs
    assert any("broke cycle" in n for n in graph.model.repair_notes)
    # Deterministic: building twice gives the same result.
    graph2 = CausalTaskGraph.from_decomposition(make_decomposition())
    assert [e.model_dump() for e in graph2.model.edges] == [
        e.model_dump() for e in CausalTaskGraph.from_decomposition(make_decomposition()).model.edges
    ]


def test_component_and_edge_caps():
    dec = CausalDecomposition(
        goal="g",
        components=[ComponentDraft(id=f"c{i}", label=f"c{i}") for i in range(MAX_COMPONENTS + 5)]
        + [ComponentDraft(id="out", label="out", kind="outcome")],
        edges=[CausalEdge(source=f"c{i}", target="out", confidence=0.5 + i * 0.01)
               for i in range(MAX_EDGES + 5)],
    )
    graph = CausalTaskGraph.from_decomposition(dec)
    assert len(graph.model.components) <= MAX_COMPONENTS
    assert len(graph.model.edges) <= MAX_EDGES
    # The outcome (goal-relevant core) survives the cap.
    assert any(c.id == "out" for c in graph.model.components)


def test_ids_are_slugified():
    dec = CausalDecomposition(
        goal="g",
        components=[ComponentDraft(id="Fancy Name!", label="x"),
                    ComponentDraft(id="other", label="y", kind="outcome")],
        edges=[CausalEdge(source="Fancy Name!", target="other")],
    )
    graph = CausalTaskGraph.from_decomposition(dec)
    assert {c.id for c in graph.model.components} == {"fancy_name", "other"}
    assert graph.model.edges[0].source == "fancy_name"


def test_critical_path_reaches_outcome():
    graph = CausalTaskGraph.from_decomposition(make_decomposition())
    assert graph.model.critical_path[-1] == "forecast"
    assert graph.model.critical_path[0] in ("data", "budget")


def test_derive_plan_topological_with_deps():
    graph = CausalTaskGraph.from_decomposition(make_decomposition())
    plan = graph.derive_plan(max_steps=8)
    # Only actionable kinds get steps (input/constraint excluded).
    step_components = [s.component_id for s in plan.steps]
    assert step_components == ["features", "model", "forecast"]
    by_component = {s.component_id: s for s in plan.steps}
    assert by_component["model"].depends_on == [by_component["features"].id]
    assert by_component["forecast"].depends_on == [by_component["model"].id]
    assert "Global pathway" in plan.rationale


def test_derive_plan_truncation_keeps_critical_path():
    dec = CausalDecomposition(
        goal="g",
        components=[ComponentDraft(id=f"p{i}", label=f"p{i}") for i in range(6)]
        + [ComponentDraft(id="out", label="out", kind="outcome")],
        edges=[CausalEdge(source="p0", target="p1", confidence=0.9),
               CausalEdge(source="p1", target="out", confidence=0.9)],
    )
    graph = CausalTaskGraph.from_decomposition(dec)
    plan = graph.derive_plan(max_steps=3)
    assert len(plan.steps) == 3
    kept = {s.component_id for s in plan.steps}
    for cid in graph.model.critical_path:
        assert cid in kept


def test_propagate_impact_is_descendants_only():
    graph = CausalTaskGraph.from_decomposition(make_decomposition())
    assert graph.propagate_impact("features") == {"model", "forecast"}
    assert graph.propagate_impact("forecast") == set()
    assert graph.propagate_impact("nonexistent") == set()


def test_invalidate_preserves_unaffected_done_steps():
    graph = CausalTaskGraph.from_decomposition(make_decomposition())
    plan = graph.derive_plan(max_steps=8)
    features, model, forecast = plan.steps
    features.status = "done"
    model.status = "failed"
    invalidated = graph.invalidate(plan, graph.propagate_impact("model"))
    assert invalidated == [forecast.id]
    assert features.status == "done"          # unaffected completed work preserved
    assert forecast.status == "invalidated"


def test_splice_scopes_ids_and_versions():
    graph = CausalTaskGraph.from_decomposition(make_decomposition())
    plan = graph.derive_plan(max_steps=8)
    features, model, forecast = plan.steps
    features.status = "done"
    model.status = "failed"
    graph.invalidate(plan, graph.propagate_impact("model"))

    request = ReplanRequest(
        failed_step=model,
        observed="singular matrix",
        affected_component_ids=["forecast"],
        invalidated_step_ids=[forecast.id],
    )
    replan = ReplanResult(
        reason="use regularized regression",
        new_steps=[
            NewStepDraft(component_id="model", objective="Refit with ridge", depends_on=[features.id]),
            NewStepDraft(component_id="forecast", objective="Recompute forecast"),
            NewStepDraft(component_id="data", objective="OUT OF SCOPE"),  # must be rejected
        ],
    )
    plan, event = graph.splice(plan, replan, request)

    assert event.new_step_ids == [f"{model.id}.r1", f"{model.id}.r2"]
    assert event.plan_version_from == 1 and event.plan_version_to == 2
    assert "rejected out-of-scope" in event.reason and "data" in event.reason
    new1 = next(s for s in plan.steps if s.id == f"{model.id}.r1")
    new2 = next(s for s in plan.steps if s.id == f"{model.id}.r2")
    assert new1.depends_on == [features.id]        # only completed deps kept
    assert new2.depends_on == [new1.id]            # sequential chaining
    assert new1.attempt == 1                       # retry of the failed component
    statuses = {c.id: c.status for c in graph.model.components}
    assert statuses["model"] == "replanned" and statuses["forecast"] == "replanned"


def test_critical_path_is_stable_across_splice():
    """critical_path is computed once in from_decomposition and never refreshed.

    That is correct only while splice leaves the graph topology alone (it adds
    plan steps and flips component status). This pins the invariant: if splice
    ever starts touching model.edges / self._g, the stored critical_path goes
    stale and this fails — see the comment at the end of splice()."""
    graph = CausalTaskGraph.from_decomposition(make_decomposition())
    plan = graph.derive_plan(max_steps=8)
    features, model, forecast = plan.steps
    model.status = "failed"

    before = list(graph.model.critical_path)
    edges_before = [(e.source, e.target) for e in graph.model.edges]

    graph.splice(
        plan,
        ReplanResult(reason="retry", new_steps=[
            NewStepDraft(component_id="model", objective="Refit with ridge"),
        ]),
        ReplanRequest(failed_step=model, observed="singular matrix",
                      affected_component_ids=["forecast"],
                      invalidated_step_ids=[forecast.id]),
    )

    assert graph.model.critical_path == before
    assert [(e.source, e.target) for e in graph.model.edges] == edges_before
    # Recomputing from the live graph agrees, i.e. the stored value is not stale.
    assert graph.critical_path() == before


def test_state_roundtrip_and_ui_graph():
    graph = CausalTaskGraph.from_decomposition(make_decomposition())
    restored = CausalTaskGraph.from_state(graph.to_state())
    assert restored.to_state() == graph.to_state()
    ui = graph.to_ui_graph()
    assert set(ui.keys()) == {"nodes", "edges", "critical_path", "version"}
    assert all(set(n.keys()) == {"id", "label", "kind", "status"} for n in ui["nodes"])
    import json
    json.dumps(ui)  # must be plain JSON


def test_subgraph_slice():
    graph = CausalTaskGraph.from_decomposition(make_decomposition())
    comps, edges = graph.subgraph_slice({"model", "forecast"})
    assert {c.id for c in comps} == {"model", "forecast"}
    assert [(e.source, e.target) for e in edges] == [("model", "forecast")]


def test_ledger_append_is_pure_and_capped():
    record = ChangeRecord(seq=1, step_id="s1", component_id="c", verdict="success")
    original = []
    ledger = append_record(original, record)
    assert original == [] and len(ledger) == 1
    for i in range(2, 60):
        ledger = append_record(ledger, ChangeRecord(seq=i, step_id=f"s{i}", component_id="c", verdict="success"))
    assert len(ledger) == 50
    assert next_seq(ledger) == 60
    assert latest_for_step(ledger, "s59")["seq"] == 59
    assert latest_for_step(ledger, "missing") is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
