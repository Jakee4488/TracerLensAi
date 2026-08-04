// What the agent intended to do, and what changed when it didn't work.
//
// Both halves were already arriving in the report and rendering nowhere:
// `causal_plan` was typed `steps?: unknown[]` and never read, and the replan
// events were computed in graph_engine.splice, flattened to one trace line and
// dropped before they left the agent. Between them they are the clearest
// statement of intent the system produces.

import { useState } from "react";
import type { ExecutionPlan, PlanStep, ReplanEvent } from "../../types";

const STATUS_LABEL: Record<PlanStep["status"], string> = {
  pending: "queued",
  running: "running",
  done: "done",
  failed: "failed",
  invalidated: "invalidated",
  skipped: "skipped",
};

function StepRow({ step, replacedBy }: { step: PlanStep; replacedBy?: ReplanEvent }) {
  const [open, setOpen] = useState(false);
  const hasDetail = !!(step.expected_effect || step.result_summary || step.depends_on.length);

  return (
    <li className={"plan-step " + step.status}>
      <div className="plan-step-head">
        {hasDetail ? (
          <button
            type="button"
            className="plan-step-toggle"
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            <span className="plan-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
            <span className="plan-objective">{step.objective || step.id}</span>
          </button>
        ) : (
          <div className="plan-step-toggle static">
            <span className="plan-caret" aria-hidden="true" />
            <span className="plan-objective">{step.objective || step.id}</span>
          </div>
        )}
        <span className={"plan-status " + step.status}>{STATUS_LABEL[step.status]}</span>
        {step.attempt > 1 && (
          <span className="plan-attempt" title="Retried after a failed attempt">
            {`attempt ${step.attempt}`}
          </span>
        )}
      </div>

      {open && (
        <dl className="plan-detail">
          {step.expected_effect && (
            <>
              <dt>expected</dt>
              <dd>{step.expected_effect}</dd>
            </>
          )}
          {step.result_summary && (
            <>
              <dt>result</dt>
              <dd>{step.result_summary}</dd>
            </>
          )}
          {step.depends_on.length > 0 && (
            <>
              <dt>depends on</dt>
              <dd>{step.depends_on.join(", ")}</dd>
            </>
          )}
          {replacedBy && (
            <>
              <dt>replaced by</dt>
              <dd>{replacedBy.new_step_ids.join(", ") || "nothing"}</dd>
            </>
          )}
        </dl>
      )}
    </li>
  );
}

function ReplanNarrative({ event }: { event: ReplanEvent }) {
  return (
    <li className="replan-event">
      <div className="replan-head">
        <span className="replan-badge">replan</span>
        <span className="replan-versions">
          {`plan v${event.plan_version_from} → v${event.plan_version_to}`}
        </span>
      </div>
      <p className="replan-body">
        <strong>{event.failed_step_id}</strong> failed
        {event.reason ? `: ${event.reason}` : "."}
      </p>
      <dl className="replan-detail">
        <dt>invalidated</dt>
        <dd>{event.invalidated_step_ids.join(", ") || "nothing downstream"}</dd>
        <dt>added</dt>
        <dd>{event.new_step_ids.join(", ") || "nothing"}</dd>
      </dl>
    </li>
  );
}

interface Props {
  plan: ExecutionPlan | null;
  replans?: ReplanEvent[] | null;
}

export function PlanView({ plan, replans }: Props) {
  const steps = plan?.steps || [];
  const events = replans || [];
  if (steps.length === 0 && events.length === 0) return null;

  // A step is superseded by the replan that names it as invalidated.
  const replacedBy = new Map<string, ReplanEvent>();
  for (const event of events) {
    for (const id of event.invalidated_step_ids) replacedBy.set(id, event);
  }

  return (
    <details className="plan-card" open>
      <summary>
        Plan
        {plan?.version != null && <span className="plan-version">{`v${plan.version}`}</span>}
      </summary>

      {plan?.rationale && <p className="plan-rationale">{plan.rationale}</p>}

      {steps.length > 0 && (
        <ol className="plan-steps">
          {steps.map((step) => (
            <StepRow key={step.id} step={step} replacedBy={replacedBy.get(step.id)} />
          ))}
        </ol>
      )}

      {events.length > 0 && (
        <>
          <h4 className="plan-subhead">What changed</h4>
          <ul className="replan-list">
            {events.map((event) => (
              <ReplanNarrative key={event.seq} event={event} />
            ))}
          </ul>
        </>
      )}
    </details>
  );
}
