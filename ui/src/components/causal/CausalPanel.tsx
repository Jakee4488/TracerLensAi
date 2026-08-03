// Causal reasoning panel. Ported from causal-agent.js:271-337.

import { useCallback, useEffect, useState } from "react";
import { CausalGraph } from "./CausalGraph";
import { EstimandCard } from "./EstimandCard";
import { StageDrawer } from "./StageDrawer";
import { StepDrawer, type DrawerTarget } from "./StepDrawer";
import { WorkflowTimeline } from "./WorkflowTimeline";
import type { Stage } from "../../lib/stages";
import type { Report, CausalGraph as CausalGraphType } from "../../types";

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

export function CausalPanel({ report, stages, liveGraph }: { report: Report; stages?: Stage[]; liveGraph?: CausalGraphType | null }) {
  const steps = report.causal_reasoning_steps || [];
  const graph = liveGraph || report.causal_graph;
  const hasGraph = !!(graph && graph.nodes && graph.nodes.length);
  const estimand = report.causal_estimand;
  const web = report.causal_web_retrieval;
  const phase = report.causal_status?.phase;

  const ledger = report.causal_ledger || [];
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  const [stageDrawer, setStageDrawer] = useState<Stage | null>(null);
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const openNode = useCallback(
    (id: string, label: string) => setDrawer({ componentId: id, title: label }),
    [],
  );
  const openStage = useCallback(
    (id: string, label: string) => {
      const stage = stages?.find((s) => s.id === id);
      if (stage) setStageDrawer(stage);
    },
    [stages],
  );

  useEffect(() => {
    const handleHighlight = (e: Event) => {
      const customEvent = e as CustomEvent<{ id: string | null }>;
      const targetIdOrLabel = customEvent.detail.id;
      setHighlighted(targetIdOrLabel);
      
      if (targetIdOrLabel && graph && graph.nodes) {
        const targetNode = graph.nodes.find(n => n.id === targetIdOrLabel || n.label === targetIdOrLabel);
        if (targetNode) {
          openNode(targetNode.id, targetNode.label);
        }
      }
    };
    window.addEventListener("highlight-node", handleHighlight);
    return () => window.removeEventListener("highlight-node", handleHighlight);
  }, [graph, openNode]);

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

      {stages && (
        <div className="pane-timeline">
           <WorkflowTimeline stages={stages} onOpenStage={openStage} />
        </div>
      )}

      {hasGraph && (
        <div className="graph-container">
          <CausalGraph graph={graph} onOpenNode={openNode} highlightedId={highlighted} />
        </div>
      )}

      {estimand && (
        <EstimandCard
          estimand={estimand}
          effect={report.causal_effect}
          counterfactual={report.causal_counterfactual}
          reconcile={report.causal_graph_reconcile}
        />
      )}

      {steps.length > 0 && (
        <details className="causal-steps-details" open>
          <summary>Causal Reasoning Trace</summary>
          <ul className="causal-steps">
            {steps.map((step, i) => (
              <StepLine key={i} step={step} />
            ))}
          </ul>
        </details>
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

      {stageDrawer && (
        <StageDrawer stage={stageDrawer} onClose={() => setStageDrawer(null)} />
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
