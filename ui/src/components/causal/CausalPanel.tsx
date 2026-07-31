// Causal reasoning panel. Ported from causal-agent.js:271-337.

import { useCallback, useState } from "react";
import { CausalGraph } from "./CausalGraph";
import { EstimandCard } from "./EstimandCard";
import { StepDrawer, type DrawerTarget } from "./StepDrawer";
import { WorkflowTimeline } from "./WorkflowTimeline";
import type { Stage } from "../../lib/stages";
import type { Report } from "../../types";

/** Trace lines are prefixed "[tag] rest"; the tag renders as a coloured pill. */
const STEP_TAG_RE = /^\[([a-z_ -]+)\]\s*(.*)$/i;

function StepLine({ step }: { step: string }) {
  const match = STEP_TAG_RE.exec(String(step));
  if (!match) return <li>{String(step)}</li>;
  const tag = match[1].toLowerCase();
  let cls = "step-tag";
  if (tag === "ok") cls += " ok";
  if (tag === "fail" || tag === "failed") cls += " fail";
  return (
    <li>
      <span className={cls}>{match[1]}</span>
      {match[2]}
    </li>
  );
}

function webLabel(web: NonNullable<Report["causal_web_retrieval"]>): string {
  if (web.mode === "dataset") return `web: ${web.row_count} rows`;
  if (web.mode === "evidence") return `web: ${(web.evidence || []).length} facts`;
  return "web: no data";
}

/**
 * The finished run's timeline, collapsed to one line.
 *
 * A nine-row timeline would dominate replayed history, so it folds into a
 * summary that expands on click.
 */
function TimelineSummary({ stages }: { stages: Stage[] }) {
  const [open, setOpen] = useState(false);
  const ran = stages.filter((s) => s.status !== "skipped" && s.status !== "pending");
  if (ran.length === 0) return null;
  const total = ran.reduce(
    (acc, s) => acc + (s.startedMs != null && s.endedMs != null ? s.endedMs - s.startedMs : 0),
    0,
  );
  const failed = ran.some((s) => s.status === "failed");

  return (
    <>
      <button
        type="button"
        className="timeline-summary"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {`${ran.length} stage${ran.length === 1 ? "" : "s"}`}
        {total > 0 && ` · ${(total / 1000).toFixed(1)}s`}
        {failed && " · failed"}
        <span className="chev" aria-hidden="true">
          ›
        </span>
      </button>
      {open && <WorkflowTimeline stages={stages} compact />}
    </>
  );
}

export function CausalPanel({ report, stages }: { report: Report; stages?: Stage[] }) {
  const steps = report.causal_reasoning_steps || [];
  const graph = report.causal_graph;
  const hasGraph = !!(graph && graph.nodes && graph.nodes.length);
  const estimand = report.causal_estimand;
  const web = report.causal_web_retrieval;
  const phase = report.causal_status?.phase;

  const ledger = report.causal_ledger || [];
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const openNode = useCallback(
    (id: string, label: string) => setDrawer({ componentId: id, title: label }),
    [],
  );

  return (
    <div className="causal-panel">
      <div className="causal-head">
        ⚯ Causal reasoning
        {phase && <span className="phase-badge">{String(phase).replace(/_/g, " ")}</span>}
        {web?.mode && (
          <span
            className={"web-badge " + web.mode}
            title={(web.sources || []).length ? web.sources.join("\n") : undefined}
          >
            {webLabel(web)}
          </span>
        )}
      </div>

      {estimand && (
        <EstimandCard
          estimand={estimand}
          effect={report.causal_effect}
          counterfactual={report.causal_counterfactual}
          reconcile={report.causal_graph_reconcile}
        />
      )}

      {stages && <TimelineSummary stages={stages} />}

      {steps.length > 0 && (
        <ul className="causal-steps">
          {steps.map((step, i) => (
            <StepLine key={i} step={step} />
          ))}
        </ul>
      )}

      {hasGraph && (
        <CausalGraph graph={graph} onOpenNode={openNode} highlightedId={highlighted} />
      )}

      {drawer && (
        <StepDrawer
          target={drawer}
          ledger={ledger}
          nodes={graph?.nodes || []}
          onClose={() => {
            setDrawer(null);
            setHighlighted(null);
          }}
          onHighlight={setHighlighted}
        />
      )}
    </div>
  );
}

/** Whether a report has anything causal worth showing a panel for. */
export function hasCausalContent(report: Report): boolean {
  return (
    (report.causal_reasoning_steps || []).length > 0 ||
    !!report.causal_graph?.nodes?.length ||
    !!report.causal_estimand ||
    !!report.causal_web_retrieval
  );
}
