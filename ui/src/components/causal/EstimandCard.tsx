// Formal identification card (DoWhy estimand / effect).
// Ported from causal-agent.js:349-434.
//
// All values originate from LLM/DoWhy output. JSX interpolation escapes by
// default, so — as in the vanilla version — these render as inert text.

import { fmtNum } from "../../lib/markdown";
import { EffectChart } from "./EffectChart";
import type {
  CounterfactualResult,
  EffectEstimate,
  GraphReconciliation,
  IdentificationResult,
} from "../../types";

function ReconcileBadge({ reconcile }: { reconcile: GraphReconciliation }) {
  const v = reconcile.verdict;
  const label =
    v === "corrected"
      ? `graph corrected (${reconcile.n_changes})`
      : v === "consistent"
        ? "data-consistent"
        : "untestable";
  const tips = (reconcile.changes || []).map((c) => `${c.kind} ${c.source}→${c.target}`);
  if ((reconcile.latent_confounders || []).length) {
    tips.push("latent: " + reconcile.latent_confounders.join(", "));
  }
  return (
    <span className={"graphfix-badge " + v} title={tips.length ? tips.join("; ") : undefined}>
      {label}
    </span>
  );
}

interface Props {
  estimand: IdentificationResult;
  effect: EffectEstimate | null;
  counterfactual: CounterfactualResult | null;
  reconcile: GraphReconciliation | null;
}

export function EstimandCard({ estimand, effect, counterfactual, reconcile }: Props) {
  const type = estimand.estimand_type || "none";
  const adjustments = estimand.adjustment_set || [];
  const instruments = estimand.instruments || [];

  return (
    <div className="estimand-card">
      <div className="estimand-head">
        Formal identification
        <span className={"estimand-chip" + (estimand.identifiable ? "" : " warn")}>
          {estimand.identifiable ? type : "not identifiable"}
        </span>
        {reconcile?.verdict && <ReconcileBadge reconcile={reconcile} />}
      </div>

      <div className="estimand-row">
        {(estimand.treatment || "?") + " → " + (estimand.outcome || "?")}
      </div>

      {!estimand.identifiable ? (
        <div className="estimand-note">
          {estimand.note || "No valid adjustment set for the stated causal graph."}
        </div>
      ) : (
        <>
          <div className="estimand-row">
            <span className="estimand-label">adjust for</span>
            {adjustments.length === 0 ? (
              <span className="adjust-pill none">nothing (no back-door confounding)</span>
            ) : (
              adjustments.map((v, i) => (
                <span className="adjust-pill" key={`${v}-${i}`}>
                  {String(v)}
                </span>
              ))
            )}
          </div>

          {instruments.length > 0 && (
            <div className="estimand-row">
              <span className="estimand-label">instruments</span>
              {instruments.map((v, i) => (
                <span className="adjust-pill" key={`${v}-${i}`}>
                  {String(v)}
                </span>
              ))}
            </div>
          )}

          {/* The interval and the refutations were text-only, which hid the one
              thing that matters most: whether the CI clears zero. */}
          {effect?.method && <EffectChart effect={effect} />}

          {counterfactual && counterfactual.delta != null && (
            <div className="estimand-row">
              <span className="estimand-label">counterfactual</span>
              <span className="effect-value">
                {`do(${counterfactual.treatment}=${fmtNum(counterfactual.intervention_value)}) vs ` +
                  `do(${counterfactual.treatment}=${fmtNum(counterfactual.baseline_value)}): ` +
                  `Δ ${counterfactual.outcome} = ${fmtNum(counterfactual.delta)}`}
              </span>
            </div>
          )}
        </>
      )}
    </div>
  );
}
