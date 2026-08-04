// Effect estimate as an interval plot, plus the refutation strip.
//
// Replaces the text-only "effect -1.42 (95% CI -1.61 to -1.23)" line. Whether
// the interval clears zero is the single most important read and was invisible
// in prose; here it is the chart's main statement.
//
// Built from positioned HTML rather than SVG: the plot is one-dimensional, so
// percentage positioning is responsive without viewBox scaling distorting the
// marker circles or the type, and the marks animate with plain transforms.

import { fmtNum } from "../../lib/markdown";
import type { EffectEstimate } from "../../types";

/** Linear scale across the data, always including zero so the rule is on-scale. */
interface Scale {
  pct: (value: number) => number;
}

function makeScale(values: number[]): Scale {
  const finite = values.filter((v) => Number.isFinite(v));
  let min = Math.min(0, ...finite);
  let max = Math.max(0, ...finite);
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = (max - min) * 0.08;
  min -= pad;
  max += pad;
  return { pct: (value) => ((value - min) / (max - min)) * 100 };
}

function scaleValues(effect: EffectEstimate): number[] {
  const values = [effect.point];
  if (effect.ci_low != null) values.push(effect.ci_low);
  if (effect.ci_high != null) values.push(effect.ci_high);
  // The refutation dots share this scale — one axis, so the shift each refuter
  // caused is directly comparable to the interval above it.
  for (const r of effect.refutations || []) {
    values.push(r.original_effect, r.new_effect);
  }
  return values;
}

function RefutationRow({
  r,
  scale,
  zero,
}: {
  r: EffectEstimate["refutations"][number];
  scale: Scale;
  zero: number;
}) {
  const from = scale.pct(r.original_effect);
  const to = scale.pct(r.new_effect);
  const state = r.passed ? "pass" : "fail";
  return (
    <>
      {/* "…_refuter" is a suffix every row carries, so it costs width and
          distinguishes nothing. The raw method stays in the title. */}
      <span className={"refute-name " + state} title={r.method}>
        {String(r.method || "").replace(/_refuter$/, "").replace(/_/g, " ")}
      </span>
      <div className="plot-track refute-track">
        <span className="effect-zero" style={{ left: `${zero}%` }} />
        <span
          className={"refute-shift " + state}
          style={{ left: `${Math.min(from, to)}%`, width: `${Math.abs(to - from)}%` }}
        />
        <span className="refute-dot original" style={{ left: `${from}%` }} />
        <span className={"refute-dot moved " + state} style={{ left: `${to}%` }} />
      </div>
      {/* Icon + word, never colour alone. */}
      <span className={"refute-verdict " + state}>
        {r.passed ? "✓" : "✗"} {state}
        {r.p_value != null && <span className="refute-p"> p={fmtNum(r.p_value, 2)}</span>}
      </span>
    </>
  );
}

export function EffectChart({ effect }: { effect: EffectEstimate }) {
  const hasInterval = effect.ci_low != null && effect.ci_high != null;
  const scale = makeScale(scaleValues(effect));
  const zero = scale.pct(0);
  const point = scale.pct(effect.point);
  const low = hasInterval ? scale.pct(effect.ci_low as number) : point;
  const high = hasInterval ? scale.pct(effect.ci_high as number) : point;

  // A 95% interval spanning zero means the sign of the effect is not resolved.
  const crossesZero =
    hasInterval && (effect.ci_low as number) <= 0 && (effect.ci_high as number) >= 0;
  const verdict = !hasInterval ? "none" : crossesZero ? "warn" : "ok";

  // One grid for the interval and every refutation row, so the plot column is
  // literally the same pixels on each — the shared scale the caption claims.
  return (
    <div className="effect-chart">
      <div className="effect-value-row">
        <span className="effect-point-value">{fmtNum(effect.point)}</span>
        <span className="effect-ci-text">
          {hasInterval
            ? `95% CI ${fmtNum(effect.ci_low)} to ${fmtNum(effect.ci_high)}`
            : "no interval reported"}
        </span>
        <span className={"effect-verdict " + verdict}>
          {verdict === "ok" ? "excludes zero" : verdict === "warn" ? "crosses zero" : "point only"}
        </span>
      </div>

      <span className="plot-gutter">effect</span>
      <div className={"plot-track effect-track " + verdict}>
        {/* Solid hairline, never dashed — a dashed rule reads as a threshold. */}
        <span className="effect-zero" style={{ left: `${zero}%` }} />
        {hasInterval && (
          <span
            className="effect-whisker"
            style={{ left: `${Math.min(low, high)}%`, width: `${Math.abs(high - low)}%` }}
          />
        )}
        <span
          className="effect-point"
          style={{ left: `${point}%` }}
          title={`point estimate ${fmtNum(effect.point)}`}
        />
      </div>
      <span className="plot-meta">
        {effect.n_obs ? `n=${effect.n_obs}` : ""}
      </span>

      {/* Ticks sit at their real position, not the container edges. */}
      <span className="plot-gutter" />
      <div className="plot-track effect-axis">
        {hasInterval && (
          <>
            <span className="axis-tick" style={{ left: `${low}%` }}>
              {fmtNum(effect.ci_low)}
            </span>
            <span className="axis-tick" style={{ left: `${high}%` }}>
              {fmtNum(effect.ci_high)}
            </span>
          </>
        )}
        <span className="axis-tick zero" style={{ left: `${zero}%` }}>
          0
        </span>
      </div>
      <span className="plot-meta" />

      {(effect.refutations || []).length > 0 && (
        <>
          <div className="refute-caption">
            robustness — original ○ vs re-estimated ●, same scale as above
          </div>
          {effect.refutations.map((r, i) => (
            <RefutationRow key={`${r.method}-${i}`} r={r} scale={scale} zero={zero} />
          ))}
        </>
      )}

      <div className="effect-method">{effect.method}</div>
    </div>
  );
}
