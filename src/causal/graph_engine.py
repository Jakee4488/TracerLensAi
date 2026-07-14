"""Deterministic causal-graph engine.

Everything here is pure computation over the pydantic models (no I/O, no LLM,
no ADK): building a validated DAG from an LLM decomposition, deriving the
global pathway (plan), propagating impact of a change through descendants,
invalidating affected steps, and splicing a localized replan into the plan.
"""

from __future__ import annotations

import networkx as nx

from src.causal.models import (
    CausalDecomposition,
    CausalEdge,
    CausalGraph,
    Component,
    ExecutionPlan,
    PlanStep,
    ReplanEvent,
    ReplanRequest,
    ReplanResult,
)
from src.causal.state_keys import MAX_COMPONENTS, MAX_EDGES

ACTIONABLE_KINDS = {"process", "artifact", "outcome"}


class CausalTaskGraph:
    """A validated causal DAG plus the deterministic operations on it."""

    def __init__(self, model: CausalGraph):
        self.model = model
        self._g = nx.DiGraph()
        for comp in model.components:
            self._g.add_node(comp.id)
        for edge in model.edges:
            self._g.add_edge(edge.source, edge.target, confidence=edge.confidence)

    # ── Construction ─────────────────────────────────────────────────────────

    @classmethod
    def from_decomposition(cls, dec: CausalDecomposition) -> "CausalTaskGraph":
        """Validate an LLM decomposition into a DAG, repairing deterministically
        (dedupe, dangling-edge drop, size caps, cycle repair) — never a second
        LLM call."""
        notes: list[str] = []

        components: list[Component] = []
        seen: set[str] = set()
        for draft in dec.components:
            comp = Component(id=draft.id, label=draft.label or draft.id,
                             kind=draft.kind, description=draft.description)
            if comp.id in seen:
                notes.append(f"dropped duplicate component '{comp.id}'")
                continue
            seen.add(comp.id)
            components.append(comp)

        if len(components) > MAX_COMPONENTS:
            keep = _select_capped_components(components, dec.edges, MAX_COMPONENTS)
            dropped = [c.id for c in components if c.id not in keep]
            notes.append(f"capped components at {MAX_COMPONENTS}; dropped {', '.join(dropped)}")
            components = [c for c in components if c.id in keep]
            seen = {c.id for c in components}

        edges: list[CausalEdge] = []
        seen_pairs: set[tuple[str, str]] = set()
        for edge in dec.edges:
            pair = (edge.source, edge.target)
            if edge.source == edge.target:
                notes.append(f"dropped self-loop on '{edge.source}'")
                continue
            if edge.source not in seen or edge.target not in seen:
                notes.append(f"dropped dangling edge {edge.source}->{edge.target}")
                continue
            if pair in seen_pairs:
                notes.append(f"dropped duplicate edge {edge.source}->{edge.target}")
                continue
            seen_pairs.add(pair)
            edges.append(edge)

        if len(edges) > MAX_EDGES:
            # Keep the strongest causal signals; stable order on ties.
            ranked = sorted(enumerate(edges), key=lambda ie: (-ie[1].confidence, ie[0]))
            kept_idx = sorted(i for i, _ in ranked[:MAX_EDGES])
            notes.append(f"capped edges at {MAX_EDGES}")
            edges = [edges[i] for i in kept_idx]

        edges, cycle_notes = _repair_cycles(components, edges)
        notes.extend(cycle_notes)

        model = CausalGraph(goal=dec.goal.strip(), components=components,
                            edges=edges, repair_notes=notes)
        graph = cls(model)
        model.critical_path = graph.critical_path()
        return graph

    def to_state(self) -> dict:
        return self.model.model_dump(mode="json")

    @classmethod
    def from_state(cls, raw: dict) -> "CausalTaskGraph":
        return cls(CausalGraph.model_validate(raw))

    def to_ui_graph(self) -> dict:
        """The exact payload the proxy forwards to the UI."""
        return {
            "nodes": [
                {"id": c.id, "label": c.label, "kind": c.kind, "status": c.status}
                for c in self.model.components
            ],
            "edges": [
                {"source": e.source, "target": e.target,
                 "relation": e.relation, "confidence": e.confidence}
                for e in self.model.edges
            ],
            "critical_path": self.model.critical_path,
            "version": self.model.version,
        }

    # ── Pathway derivation ───────────────────────────────────────────────────

    def topological_order(self) -> list[str]:
        return list(nx.lexicographical_topological_sort(self._g))

    def critical_path(self) -> list[str]:
        """Confidence-weighted longest path into the outcome nodes: the
        'global optimum pathway' highlight."""
        outcomes = [c.id for c in self.model.components if c.kind == "outcome"]
        if outcomes:
            relevant = set(outcomes)
            for out in outcomes:
                relevant |= nx.ancestors(self._g, out)
            sub = self._g.subgraph(relevant)
        else:
            sub = self._g
        if sub.number_of_nodes() == 0:
            return []
        return list(nx.dag_longest_path(sub, weight="confidence"))

    def derive_plan(self, max_steps: int) -> ExecutionPlan:
        """One step per actionable component in deterministic topological
        order; dependencies mirror causal in-edges; truncation keeps the
        critical path."""
        by_id = {c.id: c for c in self.model.components}
        order = [cid for cid in self.topological_order()
                 if by_id[cid].kind in ACTIONABLE_KINDS]

        if len(order) > max_steps:
            critical = [cid for cid in self.topological_order()
                        if cid in set(self.model.critical_path) and cid in set(order)]
            keep = list(dict.fromkeys(critical))[:max_steps]
            for cid in order:
                if len(keep) >= max_steps:
                    break
                if cid not in keep:
                    keep.append(cid)
            keep_set = set(keep)
            order = [cid for cid in order if cid in keep_set]

        step_by_component: dict[str, str] = {}
        steps: list[PlanStep] = []
        for i, cid in enumerate(order, start=1):
            comp = by_id[cid]
            step_id = f"s{i}"
            step_by_component[cid] = step_id
            effects = [e.target for e in self.model.edges if e.source == cid]
            deps = [step_by_component[e.source] for e in self.model.edges
                    if e.target == cid and e.source in step_by_component]
            objective = f"Advance '{comp.label}'"
            if comp.description:
                objective += f": {comp.description}"
            steps.append(PlanStep(
                id=step_id,
                component_id=cid,
                objective=objective,
                expected_effect=("Affects: " + ", ".join(effects)) if effects else "",
                depends_on=sorted(set(deps)),
            ))

        pathway = " -> ".join(s.id for s in steps)
        critical = " -> ".join(self.model.critical_path)
        rationale = f"Global pathway {pathway}"
        if critical:
            rationale += f" along critical path {critical}"
        return ExecutionPlan(steps=steps, rationale=rationale)

    # ── Impact propagation & replanning ──────────────────────────────────────

    def propagate_impact(self, component_id: str) -> set[str]:
        """Which components a change to ``component_id`` affects (descendants
        through the causal edges). Deterministic — no LLM."""
        if component_id not in self._g:
            return set()
        return set(nx.descendants(self._g, component_id))

    def invalidate(self, plan: ExecutionPlan, affected: set[str]) -> list[str]:
        """Mark steps on affected components invalidated. Completed steps on
        unaffected components are untouched — replanning stays localized."""
        invalidated: list[str] = []
        for step in plan.steps:
            if step.component_id in affected and step.status in ("pending", "running", "done"):
                step.status = "invalidated"
                invalidated.append(step.id)
        for comp in self.model.components:
            if comp.id in affected and comp.status in ("pending", "active", "done"):
                comp.status = "invalidated"
        return invalidated

    def splice(self, plan: ExecutionPlan, replan: ReplanResult,
               request: ReplanRequest) -> tuple[ExecutionPlan, ReplanEvent]:
        """Splice replacement steps for the failed/invalidated components into
        the plan. Out-of-scope steps are rejected deterministically."""
        failed_step = request.failed_step
        allowed = set(request.affected_component_ids) | {failed_step.component_id}
        existing_ids = {s.id for s in plan.steps}
        done_ids = {s.id for s in plan.steps if s.status in ("done", "skipped")}

        rejected: list[str] = []
        new_steps: list[PlanStep] = []
        prev_new_id: str | None = None
        for n, draft in enumerate(replan.new_steps[:request.max_new_steps], start=1):
            comp_id = draft.component_id
            if comp_id not in allowed:
                rejected.append(comp_id)
                continue
            new_id = f"{failed_step.id}.r{len(new_steps) + 1}"
            deps = [d for d in draft.depends_on if d in done_ids]
            if prev_new_id:
                deps.append(prev_new_id)
            new_steps.append(PlanStep(
                id=new_id,
                component_id=comp_id,
                objective=draft.objective,
                expected_effect=draft.expected_effect,
                depends_on=sorted(set(deps)),
                attempt=failed_step.attempt + 1 if comp_id == failed_step.component_id else 0,
            ))
            prev_new_id = new_id

        plan.steps.extend(new_steps)
        version_from = plan.version
        plan.version += 1

        # Replanned components go back into play.
        respliced = {s.component_id for s in new_steps}
        for comp in self.model.components:
            if comp.id in respliced:
                comp.status = "replanned"
        self.model.version += 1

        reason = replan.reason.strip()[:300]
        if rejected:
            reason += f" (rejected out-of-scope steps for: {', '.join(sorted(set(rejected)))})"
        event = ReplanEvent(
            seq=plan.version,
            failed_step_id=failed_step.id,
            invalidated_step_ids=list(request.invalidated_step_ids),
            new_step_ids=[s.id for s in new_steps if s.id not in existing_ids],
            plan_version_from=version_from,
            plan_version_to=plan.version,
            reason=reason,
        )
        return plan, event

    # ── Status bookkeeping ───────────────────────────────────────────────────

    def set_component_status(self, component_id: str, status: str) -> None:
        for comp in self.model.components:
            if comp.id == component_id:
                comp.status = status  # type: ignore[assignment]
                return

    def subgraph_slice(self, component_ids: set[str]) -> tuple[list[Component], list[CausalEdge]]:
        """The affected slice fed to the replanner — never the whole graph."""
        comps = [c for c in self.model.components if c.id in component_ids]
        edges = [e for e in self.model.edges
                 if e.source in component_ids and e.target in component_ids]
        return comps, edges


def _select_capped_components(components: list[Component], edges: list[CausalEdge],
                              cap: int) -> set[str]:
    """Deterministic cap: outcomes and their ancestors first (the goal-relevant
    core), then original order."""
    g = nx.DiGraph()
    ids = {c.id for c in components}
    g.add_nodes_from(ids)
    g.add_edges_from((e.source, e.target) for e in edges
                     if e.source in ids and e.target in ids and e.source != e.target)

    outcomes = [comp.id for comp in components if comp.kind == "outcome"]
    ancestors: set[str] = set()
    for out in outcomes:
        try:
            ancestors |= nx.ancestors(g, out)
        except nx.NetworkXError:
            pass

    # Outcomes first (they anchor the goal), then their ancestors, then the
    # rest — each tier in original order so selection stays stable.
    keep: list[str] = list(outcomes)
    for comp in components:
        if comp.id in ancestors and comp.id not in keep:
            keep.append(comp.id)
    for comp in components:
        if len(keep) >= cap:
            break
        if comp.id not in keep:
            keep.append(comp.id)
    return set(keep[:cap])


def _repair_cycles(components: list[Component],
                   edges: list[CausalEdge]) -> tuple[list[CausalEdge], list[str]]:
    """Break cycles by removing the lowest-confidence edge of each cycle
    (ties: lexicographic on (source, target)) until the graph is a DAG."""
    notes: list[str] = []
    edges = list(edges)
    while True:
        g = nx.DiGraph()
        g.add_nodes_from(c.id for c in components)
        g.add_edges_from((e.source, e.target) for e in edges)
        try:
            cycle = nx.find_cycle(g)
        except nx.NetworkXNoCycle:
            return edges, notes
        cycle_pairs = {(u, v) for u, v, *_ in cycle}
        in_cycle = [e for e in edges if (e.source, e.target) in cycle_pairs]
        victim = min(in_cycle, key=lambda e: (e.confidence, e.source, e.target))
        edges = [e for e in edges if e is not victim]
        notes.append(
            f"broke cycle by removing edge {victim.source}->{victim.target} "
            f"(confidence {victim.confidence:.2f})"
        )
