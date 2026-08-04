// Causal reasoning panel. Ported from causal-agent.js:271-337.

import { useCallback, useEffect, useState } from "react";
import { CausalGraph } from "./CausalGraph";
import { EstimandCard } from "./EstimandCard";
import { PlanView } from "./PlanView";
import { StepDrawer, type DrawerTarget } from "./StepDrawer";
import { WorkflowTimeline } from "./WorkflowTimeline";
import { downloadRun } from "../../lib/export";
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
  const dropped = report.causal_ledger_dropped || 0;
  // Mid-run App passes a placeholder report and drives the panel from
  // liveGraph, so there is nothing worth serialising yet.
  const finished = !!report.response;
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
        {/* An exportable run is what turns the pane from something you read
            into something you can attach to a ticket or diff. Disabled until
            the run lands — mid-flight the panel is driven by the live graph and
            an export would be a near-empty file. */}
        <button
          className="export-run-btn"
          onClick={() => downloadRun(report, stages)}
          disabled={!finished}
          title={finished ? "Download this run as JSON" : "Available once the run finishes"}
        >
          ↓ Export
        </button>
        {onClose && (
          <button className="close-pane-btn" onClick={onClose} aria-label="Close causal panel">
            ✕
          </button>
        )}
      </div>

      {report.run_id && (
        <div className="run-id-strip" title="Correlation id: joins this answer to its server log line and trace">
          <span className="run-id-label">run</span>
          <code>{report.run_id}</code>
        </div>
      )}

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

        <PlanView plan={report.causal_plan} replans={report.causal_replan_events} />

        {steps.length > 0 && (
          <details className="causal-steps-details" open>
            <summary>Causal Reasoning Trace</summary>
            <ul className="causal-steps">
              {steps.map((step, i) => (
                <StepLine key={i} step={step} />
              ))}
            </ul>
            {/* The ledger is capped; saying so beats a trail that quietly
                loses its head. */}
            {dropped > 0 && (
              <p className="ledger-truncated">
                {`${dropped} earlier ledger ${dropped === 1 ? "entry was" : "entries were"} ` +
                  `dropped when the run exceeded the ledger cap.`}
              </p>
            )}
          </details>
        )}
      </div>

      {drawer && (
        <StepDrawer
          target={drawer}
          ledger={ledger}
          nodes={graph?.nodes || []}
          edges={graph?.edges || []}
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
