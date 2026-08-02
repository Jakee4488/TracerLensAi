// Drill-down into what a step actually did.
//
// Powered entirely by causal_ledger, which the proxy now forwards (1.6).
// ChangeRecord already carries expected/observed/verdict/affected per step, so
// nothing new is computed server-side — this surfaces state that was being
// collected and thrown away.

import { useEffect, useRef } from "react";
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
  const labelById = new Map(nodes.map((n) => [n.id, n.label]));

  const records = ledger.filter((r) =>
    target.componentId ? r.component_id === target.componentId : r.step_id === target.stepId,
  );
  const node = target.componentId
    ? nodes.find((n) => n.id === target.componentId)
    : undefined;

  useEffect(() => {
    panelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

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
            <span className="drawer-kind">{node.kind}</span>
          </div>
        )}

        {records.length === 0 ? (
          <p className="drawer-empty">
            {node
              ? "This component was never executed, so the change ledger has no entry for it."
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
    </>
  );
}
