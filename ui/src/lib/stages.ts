import type { CausalGraph } from "../types";

export type StageStatus = "pending" | "active" | "done" | "failed" | "skipped";

export interface Stage {
  id: string;
  label: string;
  status: StageStatus;
  steps: string[];
  startedMs: number | null;
  endedMs: number | null;
  current?: { index: number; total: number };
}

export interface ProgressFrame {
  stage?: string | null;
  message?: string | null;
  step?: string | null;
  steps?: string[];
  elapsed_ms?: number;
  index?: number;
  total?: number;
  graph?: CausalGraph | null;
}

const STAGE_ORDER: Array<Pick<Stage, "id" | "label">> = [
  { id: "route", label: "Route" },
  { id: "web", label: "Web ingest" },
  { id: "decompose", label: "Decompose" },
  { id: "estimand", label: "Identify estimand" },
  { id: "estimate", label: "Estimate effect" },
  { id: "execute", label: "Execute plan" },
  { id: "replan", label: "Replan" },
  { id: "synthesize", label: "Synthesize" },
];

const ALIASES: Record<string, string> = {
  graph: "decompose",
  graph_build: "decompose",
  identify: "estimand",
  execute_step: "execute",
};

function normalizeStage(id: string | null | undefined): string | null {
  if (!id) return null;
  const key = id.toLowerCase().trim();
  return ALIASES[key] ?? key;
}

function cloneStage(stage: Stage): Stage {
  return {
    ...stage,
    steps: [...stage.steps],
    current: stage.current ? { ...stage.current } : undefined,
  };
}

export function initialStages(): Stage[] {
  return STAGE_ORDER.map((stage) => ({
    ...stage,
    status: stage.id === "web" ? "skipped" : "pending",
    steps: [],
    startedMs: null,
    endedMs: null,
  }));
}

export function applyProgress(prev: Stage[], frame: ProgressFrame): Stage[] {
  const stages = prev.map(cloneStage);
  const stageId = normalizeStage(frame.stage);
  const elapsed = frame.elapsed_ms ?? 0;

  if (!stageId) return stages;
  const idx = stages.findIndex((stage) => stage.id === stageId);
  if (idx < 0) return stages;

  for (let i = 0; i < idx; i += 1) {
    if (stages[i].status === "pending" || stages[i].status === "active") {
      stages[i].status = "done";
      if (stages[i].startedMs == null) stages[i].startedMs = 0;
      stages[i].endedMs = elapsed;
    }
  }

  const stage = stages[idx];
  if (stage.status === "pending" || stage.status === "skipped") {
    stage.status = "active";
    stage.startedMs = elapsed;
    stage.endedMs = null;
  }

  const line = frame.step ?? frame.message;
  if (line && (stage.steps.length === 0 || stage.steps[stage.steps.length - 1] !== line)) {
    stage.steps.push(line);
  }
  
  if (frame.steps && frame.steps.length > 0) {
    stage.steps.push(...frame.steps);
  }

  if (typeof frame.index === "number" && typeof frame.total === "number") {
    stage.current = { index: frame.index, total: frame.total };
  }

  return stages;
}

export function finalizeStages(prev: Stage[], elapsedMs: number, failed: boolean): Stage[] {
  return prev.map((stage) => {
    const next = cloneStage(stage);
    if (next.status === "active") {
      next.status = failed ? "failed" : "done";
      next.endedMs = elapsedMs;
    } else if (next.status === "pending") {
      next.status = "skipped";
    }
    return next;
  });
}
