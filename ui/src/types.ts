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
}

export interface GraphEdge {
  source: string;
  target: string;
  relation: string;
  confidence: number;
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

/** The terminal `done` frame: everything needed to render a finished turn. */
export interface Report {
  status: string;
  response: string;
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
  causal_plan: { version?: number; steps?: unknown[] } | null;
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
