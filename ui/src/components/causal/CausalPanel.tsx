// Causal reasoning panel. Ported from causal-agent.js:271-337.

import { useCallback, useEffect, useState } from "react";
import { CausalGraph } from "./CausalGraph";
import { EstimandCard } from "./EstimandCard";
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

interface Props {
  report: Report;
  stages?: Stage[];
  liveGraph?: CausalGraphType | null;
  /** Rendered into the pane's fixed header, so it never scrolls away. */
  onClose?: () => void;
}

export function CausalPanel({ report, stages, liveGraph, onClose }: Props) {
  const steps = report.causal_reasoning_steps || [];
  const graph = liveGraph || report.causal_graph;
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
      {/* Fixed header. The close control used to be absolutely positioned
          inside the scroll container, so it sat at y=-97 the moment you
          scrolled to anything worth reading. */}
      <div className="causal-head">
        <span className="causal-head-title">
          <span aria-hidden="true">⚯</span> Causal reasoning
        </span>
        {phase && <span className="phase-badge">{String(phase).replace(/_/g, " ")}</span>}
        {web?.mode && (
          <span
            className={"web-badge " + web.mode}
            title={(web.sources || []).length ? web.sources.join("\n") : undefined}
          >
            {webLabel(web)}
          </span>
        )}
        {onClose && (
          <button className="close-pane-btn" onClick={onClose} aria-label="Close causal panel">
            ✕
          </button>
        )}
      </div>

      <div className="causal-panel-scroll">
        {stages && (
          <div className="pane-timeline">
            <WorkflowTimeline stages={stages} />
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
      </div>

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
