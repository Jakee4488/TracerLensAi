// Shapes the proxy returns. Names mirror the `causal_*` session-state keys in
// src/causal/state_keys.py, because a persisted turn replays through exactly
// the same fields as a live one.

export type NodeStatus =
  | "pending" | "active" | "done" | "failed" | "invalidated" | "replanned";

export interface GraphNode {
  id: string;
  label: string;
  kind: string;
  status: NodeStatus;
  /** Why this component exists. Capped at 200 chars by the model validator. */
  description?: string;
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  confidence: number;
  /** Why this link was asserted. Capped at 200 chars by the model validator. */
  rationale?: string;
}

/** to_ui_graph() output — see src/causal/graph_engine.py. */
export interface CausalGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
  critical_path: string[];
  version: number;
}

export interface RefutationResult {
  method: string;
  original_effect: number;
  new_effect: number;
  passed: boolean;
  p_value: number | null;
}

export interface EffectEstimate {
  method: string;
  point: number;
  ci_low: number | null;
  ci_high: number | null;
  n_obs: number;
  refutations: RefutationResult[];
  note: string;
}

export interface IdentificationResult {
  treatment: string;
  outcome: string;
  identifiable: boolean;
  estimand_type: string;
  adjustment_set: string[];
  instruments: string[];
  estimand_expr: string;
  note: string;
}

export interface CounterfactualResult {
  treatment: string;
  outcome: string;
  baseline_value: number;
  intervention_value: number;
  delta: number | null;
}

export interface GraphChange {
  kind: "reverse" | "remove" | "add";
  source: string;
  target: string;
  reason: string;
}

export interface GraphReconciliation {
  verdict: "corrected" | "consistent" | "untestable";
  n_changes: number;
  changes: GraphChange[];
  latent_confounders: string[];
  note: string;
}

export interface WebRetrieval {
  mode: "dataset" | "evidence" | "none";
  row_count: number;
  n_sources: number;
  evidence: string[];
  sources: string[];
  note: string;
}

/** Change-ledger entry — src/causal/models.py ChangeRecord. */
export interface ChangeRecord {
  seq: number;
  step_id: string;
  component_id: string;
  expected: string;
  observed: string;
  verdict: string;
  affected: string[];
  plan_version: number;
  ts: string;
}

/** One executable step of the plan — src/causal/models.py PlanStep. */
export type StepStatus =
  | "pending" | "running" | "done" | "failed" | "invalidated" | "skipped";

export interface PlanStep {
  id: string;
  component_id: string;
  objective: string;
  expected_effect: string;
  depends_on: string[];
  status: StepStatus;
  attempt: number;
  result_summary: string;
}

/** src/causal/models.py ExecutionPlan. */
export interface ExecutionPlan {
  version: number;
  rationale: string;
  steps: PlanStep[];
}

/** Why the plan changed — src/causal/models.py ReplanEvent. */
export interface ReplanEvent {
  seq: number;
  failed_step_id: string;
  invalidated_step_ids: string[];
  new_step_ids: string[];
  plan_version_from: number;
  plan_version_to: number;
  reason: string;
}

/** The terminal `done` frame: everything needed to render a finished turn. */
export interface Report {
  status: string;
  response: string;
  /** Correlation id joining this answer to its proxy log line and trace. */
  run_id?: string;
  total_token_count: number;
  causal_reasoning_steps: string[];
  causal_graph: CausalGraph | null;
  causal_status: { phase?: string } | null;
  causal_estimand: IdentificationResult | null;
  causal_effect: EffectEstimate | null;
  causal_counterfactual: CounterfactualResult | null;
  causal_graph_reconcile: GraphReconciliation | null;
  causal_web_retrieval: WebRetrieval | null;
  causal_ledger: ChangeRecord[] | null;
  /** Entries lost to LEDGER_CAP, so a truncated audit trail says so. */
  causal_ledger_dropped?: number | null;
  causal_plan: ExecutionPlan | null;
  causal_replan_events?: ReplanEvent[] | null;
}

export interface ChatMessage {
  key: string;
  role: "user" | "ai" | "error" | "greeting";
  content: string;
  attachments?: string[];
  report?: Report;
  /** Captured when the run finished; absent on replayed history. */
  stages?: import("./lib/stages").Stage[];
}

export interface Attachment {
  localId: string;
  id: string | null;
  name: string;
  size: number;
  status: "uploading" | "done" | "error";
  error?: string;
}

export interface Conversation {
  chat_id: string;
  title: string;
  total_tokens: number;
  updated_at: string;
}
