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


# ── Statistical-inference results (produced deterministically by DoWhy) ──────

class RefutationResult(BaseModel):
    """One DoWhy robustness check on an estimated effect."""
    method: str
    original_effect: float = 0.0
    new_effect: float = 0.0
    passed: bool = False
    # Significance of the refuter's test when DoWhy provides it (p > 0.05 means
    # the estimate survived); None -> the tolerance fallback decided `passed`.
    p_value: Optional[float] = None


class IdentificationResult(BaseModel):
    """DoWhy's data-free identification of a treatment->outcome estimand.

    Produced from the variable-level DAG alone, so it is available even when no
    dataset was supplied — this is the formal replacement for the LLM's ad-hoc
    confounder guessing."""
    treatment: str = ""
    outcome: str = ""
    identifiable: bool = False
    estimand_type: str = ""                       # backdoor | iv | frontdoor | none
    adjustment_set: list[str] = Field(default_factory=list)
    instruments: list[str] = Field(default_factory=list)
    estimand_expr: str = ""
    note: str = ""

    @field_validator("estimand_expr", "note")
    @classmethod
    def _cap_text(cls, v: str) -> str:
        return (v or "").strip()[:300]


class EffectEstimate(BaseModel):
    """DoWhy's numeric effect estimate + refutations (data path only)."""
    method: str = ""
    point: float = 0.0
    ci_low: Optional[float] = None
    ci_high: Optional[float] = None
    n_obs: int = 0
    refutations: list[RefutationResult] = Field(default_factory=list)
    note: str = ""

    @field_validator("note")
    @classmethod
    def _cap_note(cls, v: str) -> str:
        return (v or "").strip()[:300]


class CounterfactualResult(BaseModel):
    """Rung-3 answer computed by dowhy.gcm on a fitted SCM (data path only):
    average outcome under do(T=intervention) vs do(T=baseline)."""
    treatment: str = ""
    outcome: str = ""
    baseline_value: float = 0.0
    intervention_value: float = 0.0
    baseline_outcome: float = 0.0
    intervention_outcome: float = 0.0
    delta: float = 0.0
    note: str = ""

    @field_validator("note")
    @classmethod
    def _cap_note(cls, v: str) -> str:
        return (v or "").strip()[:300]


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
    # Semantic gate for the estimand stage: the decomposer is already a
    # structured call, so this flag upgrades effect-query detection from the
    # lexical regex to the LLM's judgment at zero extra cost (OR-ed with the
    # regex in skip_unless_effect_query).
    is_effect_query: bool = False


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


VariableRole = Literal["treatment", "outcome", "confounder", "instrument", "mediator", "other"]


class CausalVariable(BaseModel):
    """A variable-level node the estimand-spec LLM emits (distinct from the
    task-level Component graph): something measurable in the world."""
    id: str
    label: str = ""
    role: VariableRole = "other"

    @field_validator("id")
    @classmethod
    def _slug_id(cls, v: str) -> str:
        return slugify(v)

    @field_validator("label")
    @classmethod
    def _cap_label(cls, v: str) -> str:
        return v.strip()[:60]


class VarEdge(BaseModel):
    """Directed causal relation between two variables (source causes target)."""
    source: str
    target: str

    @field_validator("source", "target")
    @classmethod
    def _slug_ends(cls, v: str) -> str:
        return slugify(v)


class CausalEstimand(BaseModel):
    """Structured output of the estimand-spec LLM: a variable-level causal DAG
    plus which variable is the treatment and which is the outcome, so DoWhy can
    identify the effect deterministically."""
    treatment: str
    outcome: str
    variables: list[CausalVariable] = Field(default_factory=list)
    edges: list[VarEdge] = Field(default_factory=list)
    # Counterfactual anchors (rung 3): filled by the LLM only when the query
    # names concrete values ("had price stayed at 10 instead of 12"); when
    # absent, the gcm path compares treatment quartiles from the data.
    baseline_value: Optional[float] = None
    intervention_value: Optional[float] = None

    @field_validator("treatment", "outcome")
    @classmethod
    def _slug_ends(cls, v: str) -> str:
        return slugify(v)


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
