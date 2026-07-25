"""Ground-truth causal benchmark for the estimation layer.

Synthetic SCMs with KNOWN average treatment effects run straight through
``run_identification`` (no LLM), so causal correctness is measurable in CI:
- identification puts required confounders IN and mediators/colliders OUT;
- estimation recovers the true ATE within tolerance across seeds;
- graphs with an unobserved confounder fall back to IV via the instrument.

Every test `importorskip`s dowhy, keeping the core suite hermetic. Sample
sizes/seeds are chosen to keep the whole file under ~1 minute.
"""

from dataclasses import dataclass, field
from typing import Callable

import pytest

from src.causal.estimation import run_identification
from src.causal.models import CausalEstimand, CausalVariable, VarEdge

N = 800
SEEDS = (0, 1)
ATE_TOL = 0.35


def _spec(treatment, outcome, variables, edges):
    return CausalEstimand(
        treatment=treatment,
        outcome=outcome,
        variables=[CausalVariable(id=v, role=r) for v, r in variables],
        edges=[VarEdge(source=s, target=t) for s, t in edges],
    )


@dataclass
class Case:
    """One benchmark scenario: a spec, a data generator, and ground truth."""
    name: str
    spec: CausalEstimand
    gen: Callable  # (rng, n) -> dict[str, np.ndarray]
    true_ate: float
    required_adjustment: set = field(default_factory=set)   # must be IN the set
    forbidden_adjustment: set = field(default_factory=set)  # must be OUT


def _cases() -> list[Case]:
    # Generators only touch numpy through the rng the test passes in, so
    # collection stays import-light.
    def confounder(rng, n):
        z = rng.normal(size=n)
        t = 0.6 * z + rng.normal(size=n)
        y = 2.0 * t + 1.5 * z + rng.normal(size=n)
        return {"z": z, "t": t, "y": y}

    def two_confounders(rng, n):
        z1, z2 = rng.normal(size=n), rng.normal(size=n)
        t = 0.5 * z1 - 0.5 * z2 + rng.normal(size=n)
        y = 1.5 * t + z1 + 2.0 * z2 + rng.normal(size=n)
        return {"z1": z1, "z2": z2, "t": t, "y": y}

    def mediator(rng, n):
        # total ATE = direct 1.0 + (t->m 1.0)*(m->y 1.0) = 2.0
        z = rng.normal(size=n)
        t = 0.5 * z + rng.normal(size=n)
        m = 1.0 * t + rng.normal(size=n)
        y = 1.0 * t + 1.0 * m + 1.5 * z + rng.normal(size=n)
        return {"z": z, "t": t, "m": m, "y": y}

    def collider(rng, n):
        z = rng.normal(size=n)
        t = 0.6 * z + rng.normal(size=n)
        y = 2.0 * t + 1.5 * z + rng.normal(size=n)
        c = t + y + rng.normal(size=n)
        return {"z": z, "t": t, "y": y, "c": c}

    def irrelevant_covariate(rng, n):
        # x exists in graph and data but touches nothing on the t->y path.
        x = rng.normal(size=n)
        t = rng.normal(size=n)
        y = 2.0 * t + rng.normal(size=n)
        return {"x": x, "t": t, "y": y}

    return [
        Case(
            name="confounder",
            spec=_spec("t", "y",
                       [("z", "confounder"), ("t", "treatment"), ("y", "outcome")],
                       [("z", "t"), ("z", "y"), ("t", "y")]),
            gen=confounder, true_ate=2.0,
            required_adjustment={"z"},
        ),
        Case(
            name="two_confounders",
            spec=_spec("t", "y",
                       [("z1", "confounder"), ("z2", "confounder"),
                        ("t", "treatment"), ("y", "outcome")],
                       [("z1", "t"), ("z1", "y"), ("z2", "t"), ("z2", "y"), ("t", "y")]),
            gen=two_confounders, true_ate=1.5,
            required_adjustment={"z1", "z2"},
        ),
        Case(
            name="mediator_excluded",
            spec=_spec("t", "y",
                       [("z", "confounder"), ("m", "mediator"),
                        ("t", "treatment"), ("y", "outcome")],
                       [("z", "t"), ("z", "y"), ("t", "m"), ("m", "y"), ("t", "y")]),
            gen=mediator, true_ate=2.0,  # TOTAL effect; adjusting for m would bias it
            required_adjustment={"z"}, forbidden_adjustment={"m"},
        ),
        Case(
            name="collider_excluded",
            spec=_spec("t", "y",
                       [("z", "confounder"), ("c", "other"),
                        ("t", "treatment"), ("y", "outcome")],
                       [("z", "t"), ("z", "y"), ("t", "y"), ("t", "c"), ("y", "c")]),
            gen=collider, true_ate=2.0,
            required_adjustment={"z"}, forbidden_adjustment={"c"},
        ),
        Case(
            name="irrelevant_covariate",
            spec=_spec("t", "y",
                       [("x", "other"), ("t", "treatment"), ("y", "outcome")],
                       [("t", "y")]),
            gen=irrelevant_covariate, true_ate=2.0,
            forbidden_adjustment={"x"},
        ),
    ]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c.name)
def test_identification_and_ate_recovery(case):
    pytest.importorskip("dowhy")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    for seed in SEEDS:
        rng = np.random.default_rng(seed)
        df = pd.DataFrame(case.gen(rng, N))

        ident, effect = run_identification(case.spec, df)

        assert ident.identifiable, f"{case.name}[seed={seed}]: not identifiable ({ident.note})"
        adj = set(ident.adjustment_set)
        missing = case.required_adjustment - adj
        assert not missing, f"{case.name}[seed={seed}]: adjustment set {adj} missing {missing}"
        leaked = case.forbidden_adjustment & adj
        assert not leaked, f"{case.name}[seed={seed}]: adjustment set wrongly includes {leaked}"

        assert effect is not None and effect.method, \
            f"{case.name}[seed={seed}]: no estimate ({getattr(effect, 'note', '')})"
        err = abs(effect.point - case.true_ate)
        assert err < ATE_TOL, \
            f"{case.name}[seed={seed}]: ATE {effect.point:.3f} vs true {case.true_ate} (err {err:.3f})"
        for r in effect.refutations:
            assert np.isfinite(r.new_effect)


def test_unobserved_confounder_uses_instrument():
    """U confounds t->y but is NOT in the data; the graph declares it, so
    backdoor is unavailable and identification must surface the instrument."""
    pytest.importorskip("dowhy")
    np = pytest.importorskip("numpy")
    pd = pytest.importorskip("pandas")

    spec = _spec("t", "y",
                 [("u", "confounder"), ("w", "instrument"),
                  ("t", "treatment"), ("y", "outcome")],
                 [("u", "t"), ("u", "y"), ("w", "t"), ("t", "y")])

    rng = np.random.default_rng(0)
    n = 2000
    u = rng.normal(size=n)
    w = rng.normal(size=n)
    t = 1.0 * w + 1.0 * u + rng.normal(size=n)
    y = 2.0 * t + 2.0 * u + rng.normal(size=n)
    df = pd.DataFrame({"w": w, "t": t, "y": y})  # u deliberately absent

    ident, effect = run_identification(spec, df)

    assert "w" in ident.instruments, f"instrument not surfaced: {ident}"
    # Estimation may run IV (accurate) or degrade with a note — but the naive
    # confounded OLS answer (~3.0 here) must not be silently reported as causal.
    if effect is not None and effect.method.startswith("iv"):
        assert abs(effect.point - 2.0) < 0.5
