import { useEffect, useRef } from "react";
import type { Stage } from "../../lib/stages";

interface Props {
  stage: Stage;
  onClose: () => void;
}

export function StageDrawer({ stage, onClose }: Props) {
  const panelRef = useRef<HTMLDivElement>(null);

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
        aria-label={`Details for ${stage.label}`}
        tabIndex={-1}
        ref={panelRef}
      >
        <div className="drawer-head">
          <span className="drawer-title">{stage.label}</span>
          <button className="drawer-close" aria-label="Close details" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="drawer-meta">
          <span className={"drawer-status " + stage.status}>{stage.status}</span>
          {stage.startedMs != null && stage.endedMs != null && (
            <span className="drawer-kind" style={{ marginLeft: 8 }}>
              {stage.endedMs - stage.startedMs < 1000 
                ? `${Math.round(stage.endedMs - stage.startedMs)}ms` 
                : `${((stage.endedMs - stage.startedMs) / 1000).toFixed(1)}s`}
            </span>
          )}
        </div>

        {stage.steps.length === 0 ? (
          <p className="drawer-empty">
            No detailed steps recorded for this stage.
          </p>
        ) : (
          <ul className="causal-steps" style={{ padding: "0 20px" }}>
            {stage.steps.map((step, i) => (
              <li key={i} className="step-line">
                <span className="step-text">{step}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </>
  );
}
