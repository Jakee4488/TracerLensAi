import { memo, useEffect, useRef, useState } from "react";
import type { Stage } from "../../lib/stages";

/**
 * Live elapsed counter.
 *
 * Isolated in its own leaf with its own rAF loop so ticking never re-renders
 * the timeline tree, and throttled to ~10fps because a per-frame text update
 * is pure layout cost nobody can read.
 */
function Elapsed({ startedMs, endedMs, running }: {
  startedMs: number | null;
  endedMs: number | null;
  running: boolean;
}) {
  const [now, setNow] = useState(() => performance.now());
  const base = useRef(performance.now());

  useEffect(() => {
    if (!running) return;
    base.current = performance.now();
    let raf = 0;
    let last = 0;
    const tick = (t: number) => {
      if (t - last > 100) {
        setNow(t);
        last = t;
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [running]);

  let ms: number;
  if (running) ms = now - base.current;
  else if (startedMs != null && endedMs != null) ms = endedMs - startedMs;
  else return null;

  if (ms < 0) ms = 0;
  return <span className="stage-time">{ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`}</span>;
}

const StageRow = memo(function StageRow({ stage, onOpenStage }: { stage: Stage; onOpenStage?: (id: string, label: string) => void }) {
  const running = stage.status === "active";
  return (
    <li 
      className={"stage-row " + stage.status} 
      data-stage={stage.id}
      onClick={() => onOpenStage?.(stage.id, stage.label)}
      style={{ cursor: onOpenStage ? 'pointer' : 'default' }}
    >
      <span className="stage-dot" aria-hidden="true" />
      <div className="stage-body">
        <div className="stage-head">
          <span className="stage-label" title={stage.label}>{stage.label}</span>
          {stage.current && (
            <span className="stage-counter">
              {`step ${stage.current.index} of ${stage.current.total}`}
            </span>
          )}
          <Elapsed startedMs={stage.startedMs} endedMs={stage.endedMs} running={running} />
        </div>
      </div>
    </li>
  );
});

interface Props {
  stages: Stage[];
  /** Hide stages that never ran, once the run is over. */
  compact?: boolean;
  onOpenStage?: (id: string, label: string) => void;
}

/**
 * The live loading state for a causal turn: pipeline stages light up in order,
 * with each stage's trace lines streaming in underneath it.
 *
 * Replaces the three-dot typing bubble in causal mode only — with the toggle
 * off there is no pipeline to show.
 */
export function WorkflowTimeline({ stages, compact = false, onOpenStage }: Props) {
  const visible = compact ? stages.filter((s) => s.status !== "skipped") : stages;

  return (
    <div className="workflow-timeline" role="status" aria-label="Causal pipeline progress">
      <ol className="stage-list">
        {visible.map((stage) => (
          <StageRow key={stage.id} stage={stage} onOpenStage={onOpenStage} />
        ))}
      </ol>
    </div>
  );
}
