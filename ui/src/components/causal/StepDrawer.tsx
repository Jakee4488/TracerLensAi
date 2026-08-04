// Drill-down into what a step actually did.
//
// Powered entirely by causal_ledger, which the proxy now forwards (1.6).
// ChangeRecord already carries expected/observed/verdict/affected per step, so
// nothing new is computed server-side — this surfaces state that was being
// collected and thrown away.
//
// Rendered as a sheet anchored to the bottom of the pane rather than a
// full-height panel: at 420px over a 397px graph the old layout covered 99% of
// the diagram, so clicking a node to learn about it hid the node.

import { useRef } from "react";
import { useFocusTrap } from "../../hooks/useFocusTrap";
import { KIND_LABEL, normalizeKind } from "../../lib/graph";
import type { ChangeRecord, GraphNode } from "../../types";

export interface DrawerTarget {
  /** Component id (a DAG node) or step id, depending on what was clicked. */
  componentId?: string;
  stepId?: string;
  title: string;
}

interface Props {
  target: DrawerTarget;
  ledger: ChangeRecord[];
  nodes: GraphNode[];
  onClose: () => void;
  /** Highlights a component in the DAG while its chip is hovered. */
  onHighlight: (componentId: string | null) => void;
}

export function StepDrawer({ target, ledger, nodes, onClose, onHighlight }: Props) {
  const panelRef = useRef<HTMLDivElement>(null);
  useFocusTrap(panelRef, onClose);

  const labelById = new Map(nodes.map((n) => [n.id, n.label]));

  const records = ledger.filter((r) =>
    target.componentId ? r.component_id === target.componentId : r.step_id === target.stepId,
  );
  const node = target.componentId
    ? nodes.find((n) => n.id === target.componentId)
    : undefined;

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <div
        className="step-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={`Details for ${target.title}`}
        tabIndex={-1}
        ref={panelRef}
      >
        <div className="drawer-head">
          <span className="drawer-title">{target.title}</span>
          <button className="drawer-close" aria-label="Close details" onClick={onClose}>
            ✕
          </button>
        </div>

        {node && (
          <div className="drawer-meta">
            <span className={"drawer-status " + node.status}>{node.status}</span>
            <span className="drawer-kind">{KIND_LABEL[normalizeKind(node.kind)]}</span>
          </div>
        )}

        <div className="drawer-scroll">
          {records.length === 0 ? (
            // The ledger records verifications, not executions, and it is
            // sparse by design — so this is the common branch. It used to read
            // "this component was never executed", directly contradicting the
            // `done` badge sitting above it.
            <p className="drawer-empty">
              {node
                ? "No verification was recorded for this component during the run — the change ledger only holds entries for steps the executor checked."
                : "No ledger entry for this step."}
            </p>
          ) : (
            records.map((record) => (
              <div className="drawer-record" key={`${record.seq}-${record.step_id}`}>
                <div className="drawer-record-head">
                  <span className="drawer-step-id">{record.step_id}</span>
                  <span className={"drawer-verdict " + record.verdict}>{record.verdict}</span>
                </div>

                {record.expected && (
                  <div className="drawer-field">
                    <span className="drawer-label">expected</span>
                    <p>{record.expected}</p>
                  </div>
                )}
                {record.observed && (
                  <div className="drawer-field">
                    <span className="drawer-label">observed</span>
                    <p>{record.observed}</p>
                  </div>
                )}

                {(record.affected || []).length > 0 && (
                  <div className="drawer-field">
                    <span className="drawer-label">invalidated downstream</span>
                    <div className="drawer-chips">
                      {record.affected.map((id) => (
                        <button
                          type="button"
                          className="affected-chip"
                          key={id}
                          onMouseEnter={() => onHighlight(id)}
                          onMouseLeave={() => onHighlight(null)}
                          onFocus={() => onHighlight(id)}
                          onBlur={() => onHighlight(null)}
                        >
                          {labelById.get(id) || id}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
    </>
  );
}
