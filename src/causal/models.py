"""Pydantic models for the causal reasoning pathway.

Two families:
- Runtime models (Component, CausalGraph, ExecutionPlan, ...) — everything the
  engine stores in session state; all JSON-round-trippable via
  ``model_dump(mode="json")``.
- LLM-facing schemas (CausalDecomposition, ReplanResult) — kept shallow and
  default-light because Gemini constrained decoding handles flat schemas best.

No google.adk / vertexai imports here: unit tests stay hermetic.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def slugify(value: str) -> str:
    """Normalize an id to [a-z0-9_] so graph/UI ids are always safe."""
    slug = _SLUG_RE.sub("_", value.strip().lower()).strip("_")
    return slug or "component"


ComponentKind = Literal["input", "process", "artifact", "constraint", "outcome"]
ComponentStatus = Literal["pending", "active", "done", "failed", "invalidated", "replanned"]
EdgeRelation = Literal["causes", "enables", "constrains", "informs"]
StepStatus = Literal["pending", "running", "done", "failed", "invalidated", "skipped"]
Verdict = Literal["success", "failure", "deviation"]


class Component(BaseModel):
    """A node in the causal graph: something the problem is made of."""
    id: str
    label: str = ""
    kind: ComponentKind = "process"
    description: str = ""
    status: ComponentStatus = "pending"

    @field_validator("id")
    @classmethod
    def _slug_id(cls, v: str) -> str:
        return slugify(v)

    @field_validator("label")
    @classmethod
    def _cap_label(cls, v: str) -> str:
        return v.strip()[:60]

    @field_validator("description")
    @classmethod
    def _cap_description(cls, v: str) -> str:
        return v.strip()[:200]


class CausalEdge(BaseModel):
    """Directed causal relation: source affects target."""
    source: str
    target: str
    relation: EdgeRelation = "causes"
    confidence: float = Field(0.7, ge=0.0, le=1.0)
    rationale: str = ""

    @field_validator("source", "target")
    @classmethod
    def _slug_ends(cls, v: str) -> str:
        return slugify(v)

    @field_validator("rationale")
    @classmethod
    def _cap_rationale(cls, v: str) -> str:
        return v.strip()[:200]


class CausalGraph(BaseModel):
    """The full causal model of the problem."""
    goal: str = ""
    components: list[Component] = Field(default_factory=list)
    edges: list[CausalEdge] = Field(default_factory=list)
    critical_path: list[str] = Field(default_factory=list)
    repair_notes: list[str] = Field(default_factory=list)
    version: int = 1


class PlanStep(BaseModel):
    """One executable step of the global pathway, tied to a component."""
    id: str
    component_id: str
    objective: str
    expected_effect: str = ""
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = "pending"
    attempt: int = 0
    result_summary: str = ""

    @field_validator("result_summary")
    @classmethod
    def _cap_result(cls, v: str) -> str:
        return v.strip()[:300]


class ExecutionPlan(BaseModel):
    """Ordered plan derived from the causal graph."""
    steps: list[PlanStep] = Field(default_factory=list)
    version: int = 1
    rationale: str = ""


class ChangeRecord(BaseModel):
    """Change-ledger entry: what a step changed and what that affects."""
    seq: int
    step_id: str
    component_id: str
    expected: str = ""
    observed: str = ""
    verdict: Verdict
    affected: list[str] = Field(default_factory=list)
    plan_version: int = 1
    ts: str = ""


class ReplanEvent(BaseModel):
    """Record of a localized replan spliced into the plan."""
    seq: int
    failed_step_id: str
    invalidated_step_ids: list[str] = Field(default_factory=list)
    new_step_ids: list[str] = Field(default_factory=list)
    plan_version_from: int
    plan_version_to: int
    reason: str = ""


class CausalStatus(BaseModel):
    """Pipeline phase + counters, stored in state and shown in the UI badge."""
    phase: Literal[
        "decomposing", "planning", "executing", "replanning",
        "synthesizing", "complete", "failed", "budget_exhausted",
    ] = "decomposing"
    executed_steps: int = 0
    replans_used: int = 0
    plan_version: int = 1
    note: str = ""


# ── LLM-facing schemas (kept shallow for constrained decoding) ───────────────

class ComponentDraft(BaseModel):
    """Component as the decomposer LLM emits it (no runtime status)."""
    id: str
    label: str
    kind: ComponentKind = "process"
    description: str = ""


class CausalDecomposition(BaseModel):
    """Structured output of the decomposer: components + causal edges."""
    goal: str
    components: list[ComponentDraft]
    edges: list[CausalEdge]


class NewStepDraft(BaseModel):
    """Replacement step as the replanner LLM emits it."""
    component_id: str
    objective: str
    expected_effect: str = ""
    depends_on: list[str] = Field(default_factory=list)


class ReplanResult(BaseModel):
    """Structured output of the replanner, scoped to the affected subgraph."""
    reason: str
    new_steps: list[NewStepDraft]


class ReplanRequest(BaseModel):
    """Deterministic controller → replanner handoff (stored in state)."""
    failed_step: PlanStep
    observed: str = ""
    affected_component_ids: list[str] = Field(default_factory=list)
    invalidated_step_ids: list[str] = Field(default_factory=list)
    subgraph_components: list[Component] = Field(default_factory=list)
    subgraph_edges: list[CausalEdge] = Field(default_factory=list)
    max_new_steps: int = 4


def parse_model(model_cls: type[BaseModel], raw) -> Optional[BaseModel]:
    """Defensively parse an LLM/state payload that may be a dict, JSON string,
    or model instance (ADK's output_key value shape varies across versions)."""
    import json

    if raw is None:
        return None
    if isinstance(raw, model_cls):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        # Tolerate a fenced code block around the JSON.
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z0-9_-]*\s*|\s*```$", "", text)
        try:
            raw = json.loads(text)
        except (ValueError, TypeError):
            return None
    if isinstance(raw, dict):
        try:
            return model_cls.model_validate(raw)
        except Exception:
            return None
    return None
