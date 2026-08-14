# Evaluation & Testing

How TracerLensAi's behavior is verified — for **both** chat pathways (the causal
reasoning pipeline and the non-causal general assistant), covering how each is
**executed** under test and how its output is **scored**.

There are two independent layers, and they answer different questions:

| Layer | Question it answers | Network / LLM? | Speed | Where |
|---|---|---|---|---|
| **`pytest` suite** | *Is the deterministic machinery correct?* (routing, graph ops, plan derivation, replan splicing, proxy transport, UI) | No LLM; offline | Seconds | [`tests/`](../tests/) |
| **`agents-cli eval`** | *Are the answers the agent actually generates good?* (correctness, grounding, quality) | Yes — runs the real agent + LLM judges | Minutes | [`tests/eval/`](../tests/eval/) |

The `pytest` layer is the CI gate: it proves the pipeline's control flow is
sound without spending a token. The `eval` layer is the quality flywheel: it
runs the live agent over curated prompts and grades the generated answers. You
need both — passing unit tests with a hallucinating model is still a broken
product, and a high eval score with broken plumbing is luck.

For the pipeline internals these tests exercise, see the
[Causal Reasoning Pipeline](causal_reasoning.md) deep-dive. For where files live,
see the [Repository Structure Guide](repository_structure.md).

---

## 1. The two chat pathways (recap)

Every message is routed by [`CausalRouterAgent`](../src/causal/router.py) with
**zero LLM calls**, purely on the presence of the `[[causal:on]]` control
marker:

```text
message ──▶ CausalRouterAgent
              │
              ├─ no marker  ─▶ general_assistant        (1 LLM call, BuiltInCodeExecutor)
              │
              └─ [[causal:on]] ─▶ CausalPipeline
                                    [web] ▶ decompose ▶ [identify/estimate] ▶ execute-loop ▶ synthesize
```

(The `[web]` and `[identify/estimate]` stages are skip-gated — web runs only under `[[web:on]]`, formal identification only for treatment-effect questions — so a simple causal query still runs just decompose ▶ execute-loop ▶ synthesize.)

Evaluation must exercise **both** branches, and the only thing that selects the
branch is the marker. That single fact drives the whole eval-dataset design:

- A case **without** the marker tests the **non-causal** path (general assistant).
- A case **with** `[[causal:on]]` tests the **causal pipeline** end-to-end.

If every eval prompt lacked the marker, the causal pipeline — the product's core
— would never run and could regress invisibly. (That was literally the state of
the original scaffold dataset: three generic prompts, none marked.)

---

## 2. The evaluation flywheel

Quality work is iterative. The `agents-cli eval` surface implements a loop:

```text
   ┌───────────────────────────────────────────────────────────┐
   │                                                           ▼
 (1) dataset ──▶ (2) generate ──▶ (3) grade ──▶ (4) analyze ──▶ (5) fix
   ▲   cases          run agent       score        read           edit agent
   │   + references   → traces        → results     rationales     or dataset
   └───────────────────────────────────────────────────────────┘
                         (6) compare  ── prove the fix moved the target metric
                                         without regressing the passing cases
```

| Stage | Command | Output |
|---|---|---|
| 2. Run inference | `agents-cli eval generate` | `artifacts/traces/traces_<ts>.json` |
| 3. Grade | `agents-cli eval grade` | `artifacts/grade_results/results_<ts>.{json,html}` |
| 4. Analyze (opt) | `agents-cli eval analyze` | failure clusters |
| 6. Compare | `agents-cli eval compare BASE NEW` | per-metric diff |

`generate` + `grade` is the day-to-day loop; `agents-cli eval run` chains them.

---

## 3. How a case is **executed** (`generate`)

`agents-cli eval generate` reads the dataset, loads the **local ADK agent** named
by `agent_directory: src` in [`agents-cli-manifest.yaml`](../agents-cli-manifest.yaml),
and runs it once per case. Two properties matter for reproducibility:

- **Fresh session per case.** Each case gets a new `InMemorySessionService` and
  session id, so cases can't leak state into each other. (Consequence:
  *cross-session* behaviour like Memory-Bank recall can't be tested here — that's
  what the `pytest` integration tests are for.)
- **Same `.env` as runtime.** `generate` loads the project `.env`, so the agent
  authenticates and picks its model endpoint exactly as it would in production.

The result is a **trace** per case: the ordered events (author, text, any tool
calls, state deltas) the agent produced. That trace is the input to grading.

### 3a. Non-causal execution (general assistant)

Prompt: `"What is the capital of France?"` — no marker.

```text
user prompt ─▶ CausalRouterAgent  (marker absent → delegate)
             └▶ general_assistant  (1 LLM call, may run Python via code executor)
                └▶ final text answer
```

The trace has **one turn, one authoring agent** (`general_assistant`), and the
final text is the answer. Simple and cheap.

### 3b. Causal execution (pipeline)

Prompt: `"[[causal:on]] ... estimate the ATE of X on Y adjusting for Z ..."`

```text
user prompt ─▶ CausalRouterAgent
                 │  strips markers, RESETS causal_* state,
                 │  seeds budgets + causal_query  ◀── see note below
                 ▼
             [CausalWebSearch]      → CSV data / evidence (only under [[web:on]])
                 ▼  (CausalWebIngestor parses it, 0 LLM)
              CausalDecomposer      → components + causal edges (structured JSON)
                 ▼  (build_graph_and_plan)
             [CausalEstimandSpec]   → variable DAG + treatment/outcome (effect queries only)
                 ▼  (CausalEstimator: DAG discovery + DoWhy identify/estimate, 0 LLM)
              CausalExecutorLoop    → per step: CausalStepExecutor (Python) →
                 │                    CausalStepController (verdict/impact) →
                 │                    CausalReplanner (only if a step failed)
                 ▼
              CausalSynthesizer     → final user-facing answer
```

The trace has **one turn but many authoring agents** (optionally `CausalWebSearch`,
then `CausalDecomposer`, optionally `CausalEstimandSpec`, `CausalStepExecutor` ×N,
`CausalReplanner`, `CausalSynthesizer`). The last `CausalSynthesizer` event is the
answer that gets graded. The bracketed stages are skip-gated (§1), so a plain
causal query without data authors only decompose ▶ execute ▶ synthesize.

> **Data-plumbing note (why `causal_query` exists).** The `CausalStepExecutor`
> runs with `include_contents="none"` to bound its context — which means it does
> **not** see the user's message. The router therefore persists the marker-stripped
> problem text to `causal_query` state, and
> [`step_executor_instruction`](../src/causal/prompts.py) injects it as
> *"Given problem and data"*. Without this, a computational step (e.g. "compute
> the ATE from these counts") has nothing to compute from, fails, triggers
> replans until the budget is exhausted, and the synthesizer honestly reports
> failure. This was a real bug the eval caught — see §7.

---

## 4. How a case is **scored** (`grade`)

`agents-cli eval grade` scores each trace against the metrics declared in
[`tests/eval/eval_config.yaml`](../tests/eval/eval_config.yaml). Metrics come in
two flavours:

### 4a. Built-in metrics (Vertex eval service)

Run server-side via the Gemini Enterprise Agent Platform eval service (needs
`GOOGLE_CLOUD_PROJECT` + ADC; graded at the `global` endpoint by default).

| Metric | What it measures | Why we use it here |
|---|---|---|
| `final_response_quality` | Overall quality of the final answer, reference-free (0–1) | Catches unclear / incomplete / unpolished answers |
| `hallucination` | Are the answer's claims supported by the trace? (0–1) | **Critical** — the agent runs Python and could report numbers the code never produced |

> **Why `tool_use_quality` is deliberately NOT used.** This agent's Python runs
> through `BuiltInCodeExecutor`, a Gemini **model-internal** tool. Its executions
> do **not** appear as `function_call` / `function_response` events in the trace.
> `tool_use_quality` requires those events and errors on **every** case
> (*"requires tool calls in the evaluation trace … no function_call … found"*).
> It's the wrong metric for built-in code execution; grounding is covered by
> `hallucination` + the custom judge's `grounding` axis instead. The multi-turn
> metrics (`multi_turn_*`) are likewise omitted — our cases are single-turn.

### 4b. Custom metrics (local, in-process)

Defined in [`tests/eval/metrics.py`](../tests/eval/metrics.py) and wired via
`custom_function_file` in the config. These run **in-process** through
`google-genai` (no managed service), so grading works on Vertex *or* AI Studio.

**`custom_response_quality` — the multi-axis causal judge.** Instead of one flat
1–5 score, an LLM judge scores four axes and blends them, weighting causal
correctness highest:

| Axis | Weight | What it checks |
|---|---:|---|
| `causal_correctness` | **0.55** | Correlation ≠ causation; confounders vs mediators vs colliders; correct adjustment set; right sign/size of effect |
| `grounding` | 0.20 | Quantities/claims supported by the trace, not fabricated |
| `relevance` | 0.15 | Answers what was asked |
| `clarity` | 0.10 | Clear, structured, states assumptions |

The aggregate (1–5) is returned as the metric `score`; the per-axis breakdown is
returned in the `explanation` (e.g. `[causal_correctness=5, grounding=5, …]`), so
a failing case tells you *which* axis broke. When a case has a golden `reference`,
the judge is told to penalise `causal_correctness` for disagreeing with it.
Grading is deterministic (`temperature=0`).

**`agent_turn_count`** — a trivial deterministic metric (counts turns); a template
for adding your own `custom_function` checks.

### 4d. Deterministic assertions (no LLM, no network, no cost)

The two metrics above ask a model for an **opinion**. These two compute a
**fact**, so they cannot drift when the judge model changes:

| Metric | File | Asserts |
|---|---|---|
| `numeric_accuracy` | [`metric_numeric_accuracy.py`](../tests/eval/metric_numeric_accuracy.py) | The agent's number is within tolerance of the known ground truth |
| `causal_node_path` | [`metric_node_path.py`](../tests/eval/metric_node_path.py) | The reasoning passed through the expected causal nodes |

Both are thin entry points over [`assertions.py`](../tests/eval/assertions.py)
(one `evaluate` per file is what `custom_function_file` expects); expectations
and tolerances live in [`expectations.json`](../tests/eval/expectations.json),
keyed by `eval_case_id`. Score is the **fraction of that case's declared checks
that passed**. A case with no expectations scores 1.0 and says *"not
applicable"* — that is absence of a check, not evidence of correctness.

**Tolerance is configurable per metric**, resolved most-specific-first:

1. the individual check's own `tolerance`
2. `tolerance_defaults.<metric_name>` in `expectations.json`
3. the global fallback in `assertions.py`

A value passes if it is within `abs` **OR** within `rel` (union, not
intersection): relative bands are useless near zero, absolute bands are useless
at scale, and both kinds of target are checked in the same run. Partial
overrides merge, so overriding only `rel` keeps the metric's `abs`.

**Where the numbers come from.** A check's `source` is either `answer` (parse
the final prose) or `node:<node_id>.<value>` (read a typed float from a node
trace — see [Causal Reasoning §7.1](causal_reasoning.md)). Node sources are
preferred and stricter: they need no parsing and they can target an
**intermediate** result. Both use the same parser the step controller uses
(`src/causal/numeric.py`), so grader and agent never disagree about what number
was written; numbers inside attached-file blocks and fenced code are excluded,
so a case cannot pass on an echo of its own fixture data.

**The node-path metric is this architecture's answer to tool-trajectory
scoring.** The agent has no `FunctionTool`s — Vertex tool isolation forbids them
— so there is no `function_call` sequence to score, which is why
`tool_use_quality` was dropped (§4a). The equivalent observable is the sequence
of causal nodes the pipeline traversed, and that is what this asserts:

```jsonc
"mediator_must_not_be_adjusted": {
  "nodes": {
    "require_kinds": ["identification"],   // the stage actually ran
    "expect_visited": ["identification"],  // the node was reached
    "adjustment_set_includes": ["family"], // confounder IS adjusted for
    "adjustment_set_excludes": ["skill"],  // mediator is NOT
    "require_identifiable": true
  }
}
```

That last pair is the point. `adjustment_set_*` reads the **identification
node's own output**, so "must not adjust for the mediator" is checked against
the formal estimand rather than against the prose — a model can write *"we must
not control for the mediator"* while its estimand adjusts for it anyway, and
only this check catches that. Node ids match tolerantly (normalized
containment), because ids are slugified LLM output and `season` may arrive as
`season_indicator`; exact matching would fail on paraphrase rather than on
substance. `expect_order` is a **subsequence**, not equality, so a replan retry
between two required nodes is legitimate.

**These need the traces.** Run generate with `CAUSAL_NODE_TRACE=1`:

```bash
CAUSAL_NODE_TRACE=1 agents-cli eval generate \
  --dataset tests/eval/datasets/causal-inference-dataset.json
agents-cli eval grade
```

Without it, node-level checks **fail loudly** (`"no node traces in this trace"`)
rather than passing silently — a silent skip is indistinguishable from a pass,
which is the one failure mode that makes a check worse than not having it.

**Field model available to any metric:** `{prompt}` (user message), `{response}`
(final answer), `{agent_data}` (full trace of turns/events — inspect tool calls
and intermediate reasoning here), and `{reference}` (golden answer, only when the
case defines one).

### 4c. Why two judges (built-in + custom)

They measure **different things**, and keeping both is the point:

- `custom_response_quality` cares about **causal correctness** — a fluent answer
  with the wrong causal claim scores low.
- `final_response_quality` cares about **user-facing answer quality** — a
  correct answer that leaks internal machinery scores low.

When they disagree, that disagreement localises the defect (see §7).

---

## 5. The dataset

[`tests/eval/datasets/basic-dataset.json`](../tests/eval/datasets/basic-dataset.json)
holds five cases spanning both pathways, each with a golden `reference` answer:

| `eval_case_id` | Marker | Pathway | Tests |
|---|:---:|---|---|
| `sanity_capital_lookup` | — | general | Basic factual sanity |
| `confounder_ice_cream_drowning` | — | general | Correlation ≠ causation (common cause) |
| `backdoor_adjustment_set` | — | general | Adjust for confounder, **not** mediator |
| `causal_pipeline_ate_estimation` | `[[causal:on]]` | **pipeline** | Numeric ATE via backdoor adjustment (needs code) |
| `causal_pipeline_confounder_id` | `[[causal:on]]` | **pipeline** | Confounder identification + estimation strategy |

The first three run the general assistant; the last two force the causal pipeline
via the marker. Each `reference` is a hand-verified golden answer (the ATE case's
reference states the exact `+0.139` result and the stratum math).

### 5a. The causal-inference dataset

[`tests/eval/datasets/causal-inference-dataset.json`](../tests/eval/datasets/causal-inference-dataset.json)
drives the same ground truths **end-to-end through the causal pipeline** (every
prompt carries `[[causal:on]]`), exercising the formal-identification stage the
basic dataset doesn't:

| `eval_case_id` | Data? | Tests |
|---|:---:|---|
| `estimand_no_data_confounders` | — | Data-free back-door identification; must name the adjustment set and **not** invent a number |
| `ate_recovery_confounder_csv` | CSV | ATE recovery adjusting for a confounder (true ATE ≈ +2.0) |
| `price_elasticity_season_csv` | CSV | Negative effect with a confounder (true slope ≈ −1.5) |
| `mediator_must_not_be_adjusted` | — | The mediator trap — must **not** adjust for a mediator |
| `collider_must_not_be_adjusted` | — | The collider trap — must **not** condition on a collider |
| `counterfactual_price_csv` | CSV | `do()`-contrast counterfactual (Δ ≈ +3) |

Graded by the same multi-axis judge (§4b), whose `causal_correctness` axis is
weighted highest. Run it explicitly:
`agents-cli eval generate --dataset tests/eval/datasets/causal-inference-dataset.json`
then `agents-cli eval grade`.

> **Extending it:** copy a case, give it a unique `eval_case_id`, and add a
> `reference`. To test the pipeline, prefix the prompt with `[[causal:on]]`.
> Start with 1–2 cases, get them passing, then expand. See the
> [datasets README](../tests/eval/datasets/README.md).

---

## 6. Running an evaluation

### 6a. Prerequisites (one-time)

Because the agents use ADK **instruction providers** (callables), two
compatibility pieces exist:

- [`pyproject.toml`](../pyproject.toml) — mirrors `requirements.txt` and adds an
  `eval` extra, so `agents-cli eval` (which runs `uv sync --dev --extra eval`)
  can resolve the environment. `requirements.txt` stays the source of truth for
  Docker/prod, and `uv.lock` is committed with `uv lock --check` enforced in CI.
- [`src/_eval_compat.py`](../src/_eval_compat.py) — a behavior-preserving shim
  (hooked from [`src/agent.py`](../src/agent.py)) that lets the Vertex eval SDK
  serialise the causal agents' **callable** instructions instead of crashing on
  them. It only affects eval *metadata*; runtime inference is untouched. (The CLI
  already patches a sibling SDK bug the same way.)

**Authentication.** Eval runs the agent locally against **Vertex AI**, so the
environment must route `google-genai` to Vertex:

```bash
# .env (already set in this repo):
GOOGLE_CLOUD_PROJECT=<project>
GOOGLE_GENAI_USE_VERTEXAI=true     # ← without this, the agent falls back to the
                                   #   Gemini API-key path and fails "No API key"
```

with Application Default Credentials present (`gcloud auth application-default login`).

### 6b. The commands

```bash
# 1. Run the agent over all cases → writes artifacts/traces/traces_<ts>.json
agents-cli eval generate

# 2. Score the traces → writes artifacts/grade_results/results_<ts>.{json,html}
agents-cli eval grade

# (grade reads EVERY file in artifacts/traces/ by default — to score just one run,
#  point at it explicitly:)
agents-cli eval grade --traces artifacts/traces/traces_<ts>.json

# 3. Prove a change helped, without regressing others
agents-cli eval compare artifacts/grade_results/results_<old>.json \
                        artifacts/grade_results/results_<new>.json
```

Open the `results_<ts>.html` in a browser for per-case rubric verdicts and judge
rationales — that's the input to every fix decision.

> `artifacts/` is run output (traces + reports), not source — but it is
> **currently tracked in git**. Either add it to `.gitignore` or accept that
> every eval run dirties the working tree.

---

## 7. Interpreting results — a worked example

The current dataset passes cleanly (all five cases green on both judges). It got
there by using the two-judge disagreement to isolate two *distinct* defects in
the causal pipeline. The ATE case's journey:

| Stage | `final_response_quality` | `custom` (causal) | Diagnosis |
|---|:---:|:---:|---|
| Baseline | 0.0 | 1.6 | Both judges fail it |
| After **data** fix | 0.0 | **4.9** | Reasoning now correct; answer still bad |
| After **presentation** fix | **1.0** | 4.9 | Fully passing |

1. **Baseline — both low.** The trace showed every executor step reporting
   *"Missing input: raw_counts"*, replanning until the budget exhausted. Root
   cause: the executor never received the data (§3b). → Fixed by threading
   `causal_query` to the executor. Causal correctness jumped `1.6 → 4.9`.

2. **Correct but ugly — judges disagree.** Now the custom judge said 4.9 (the
   math was right: ATE = 0.1389) but `final_response_quality` stayed at **0.0**.
   Reading the answer explained it: the synthesizer was leaking internal
   machinery — step ids (`s6.r1`), a *"Replanning"* section, *"What Remains
   Undone"*, "budget exhausted". A correct answer wrapped in engine internals
   reads as unfinished. → Fixed by tightening
   [`synthesizer_instruction`](../src/causal/prompts.py) to lead with the answer
   and forbid internal references. `final_response_quality` went `0.0 → 1.0`.

The lesson: **a single quality score would have hidden this.** Splitting *causal
correctness* from *answer quality* is what let each fix be found and verified
independently.

---

## 8. The `pytest` layer (deterministic, offline)

The eval layer proves the *answers* are good; the `pytest` suite proves the
*machinery* is correct — with **no network and no LLM**, so it's fast and runs on
every push. [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs three
things: `uv lock --check` (the lockfile must agree with `pyproject.toml` —
added after a drifted lock broke production), `pytest tests/ --ignore=tests/ui_tests`,
and the UI's `npm ci && lint && typecheck && build`.

| Test file | Layer | What it guards |
|---|---|---|
| [`test_causal_agents.py`](../tests/test_causal_agents.py) | Agent wiring | The **Vertex isolation invariant**: every `LlmAgent` carries at most one of `{code_executor, output_schema, tools}`; marker routing helpers. Constructs agents with **no credentials**. |
| [`test_causal_pipeline_flow.py`](../tests/test_causal_pipeline_flow.py) | Control flow | Drives the deterministic pieces (decomposer callback, controller, replan splice) against **fake contexts** exactly as the `LoopAgent` sequences them — plan derivation, success advance, failure + impact propagation, localized replan, budget exhaustion, graceful degradation. **No LLM, no ADK runtime.** |
| [`test_causal_runtime.py`](../tests/test_causal_runtime.py) | Runtime | Runtime wiring of the pipeline. |
| [`test_causal_engine.py`](../tests/test_causal_engine.py) | Graph ops | The deterministic `networkx` graph engine (construction, impact, subgraph). |
| [`test_causal_estimation.py`](../tests/test_causal_estimation.py) | Identification | DoWhy identification (confounder/mediator/collider), effect recovery + refutation, `gcm` counterfactuals, dataset/header/web-output parsing, and the effect/counterfactual query gates. `importorskip("dowhy")`. |
| [`test_causal_discovery.py`](../tests/test_causal_discovery.py) | DAG discovery | Data-driven DAG correction: a correct DAG is left untouched; a reversed edge / omitted confounder is corrected; untestable/no-data guards; never-raises. `importorskip("causallearn")`. |
| [`test_causal_benchmark.py`](../tests/test_causal_benchmark.py) | Ground truth | Synthetic SCMs with known ATEs (confounders, mediator/collider traps, unobserved confounder → IV) across seeds. `importorskip("dowhy")`. |
| [`test_causal_complexity.py`](../tests/test_causal_complexity.py) | Budgeting | Complexity tiering → budget sizing from query text. |
| [`test_causal_node_trace.py`](../tests/test_causal_node_trace.py) | Instrumentation | Node-trace recording, chained writes in one `state_delta`, cap eviction + drop counting, and the numeric parser (signs, percents, thousands, scientific notation, attachment/fence exclusion, union tolerance, determinism). |
| [`test_eval_assertions.py`](../tests/test_eval_assertions.py) | Eval checks | The deterministic metrics: trace extraction, three-level tolerance precedence, numeric checks against answer *and* node values, and the node-path assertions — including the mediator/collider worked examples. |
| [`test_causal_prompts.py`](../tests/test_causal_prompts.py) | Prompts | Instruction providers render against real and empty state, and emit `[Node: <label>]` in the form the UI's citation linkifier expects. |
| [`test_main.py`](../tests/test_main.py) | Proxy | The Cloud Run proxy (FastAPI `TestClient`) against `proxy/memstore.py` — routing, history, uploads, and token accounting including the aborted-stream path. |
| [`test_main_causal.py`](../tests/test_main_causal.py) | Proxy transport | The causal transport: marker detection and `state_delta` streaming through the proxy. |
| [`test_access.py`](../tests/test_access.py) | Access gate | Login branches, single-use links, revocation, address validation, quota accounting, extensions, erasure, retention. |
| [`test_admin.py`](../tests/test_admin.py) | Admin | OTP two-factor, per-endpoint session enforcement, one-click link tampering, dashboard injection, the retention sweep. |
| [`test_app_entrypoint.py`](../tests/test_app_entrypoint.py) | Packaging | The production ASGI entrypoint imports and serves — added after a deploy failed on an import that no test exercised. |
| [`tests/ui_tests/`](../tests/ui_tests/) | UI (E2E) | **Playwright** browser tests against the mock-mode proxy. **Not run by CI** — they need a built bundle (`npm run build`) and a browser. Run locally. |

```bash
# Backend suite (what CI runs) — fast, offline
python -m pytest tests/ --ignore=tests/ui_tests -v

# Just the causal machinery
python -m pytest tests/test_causal_agents.py tests/test_causal_pipeline_flow.py \
                 tests/test_causal_engine.py tests/test_causal_complexity.py -q

# UI E2E (needs: pip install -r requirements-dev.txt && playwright install chromium)
python -m pytest tests/ui_tests
```

### How the two layers divide the work

```text
                      causal pipeline correctness
   pytest (offline)  ◀──────────────┼──────────────▶  eval (live agent + judges)
   • routing/markers                │                 • answer correctness
   • graph & plan ops               │                 • grounding / no hallucination
   • replan splicing                │                 • answer quality/clarity
   • proxy transport                │                 • causal reasoning axes
   • fails a build in seconds       │                 • catches model regressions
```

A change to deterministic Python (router, controller, graph engine) is caught by
`pytest`. A change to a **prompt or model** — which `pytest` can't see because it
doesn't call the LLM — is caught by `eval`. Run `pytest` on every change; run
`eval generate && eval grade` after any prompt/model/instruction edit, and
`eval compare` to prove it helped.

---

## 9. Quick reference

```bash
# ─── Deterministic tests (offline, CI gate) ───────────────────────────
python -m pytest tests/ --ignore=tests/ui_tests -v

# ─── Quality eval (live agent, needs Vertex ADC + GOOGLE_GENAI_USE_VERTEXAI=true) ───
# CAUSAL_NODE_TRACE=1 is required for the node-level checks (§4d).
CAUSAL_NODE_TRACE=1 agents-cli eval generate   # run agent → traces
agents-cli eval grade                           # score traces → results
agents-cli eval compare BASE.json NEW.json      # regression check
agents-cli eval metric list                     # list built-in metrics
```

| Artifact | Path |
|---|---|
| Eval dataset | [`tests/eval/datasets/basic-dataset.json`](../tests/eval/datasets/basic-dataset.json) |
| Metric config | [`tests/eval/eval_config.yaml`](../tests/eval/eval_config.yaml) |
| Custom judge | [`tests/eval/metrics.py`](../tests/eval/metrics.py) |
| Deterministic expectations + tolerances | [`tests/eval/expectations.json`](../tests/eval/expectations.json) |
| Deterministic check engine | [`tests/eval/assertions.py`](../tests/eval/assertions.py) |
| Generated traces | `artifacts/traces/` |
| Grade results (JSON + HTML) | `artifacts/grade_results/` |
| pytest suite | [`tests/`](../tests/) |

See also: [Causal Reasoning Pipeline](causal_reasoning.md) ·
[Developer Guide](developer_guide.md) ·
[Repository Structure](repository_structure.md).
