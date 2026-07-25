# Causal Reasoning Pipeline

The causal-reasoning pipeline is TracerLensAi's core capability. When the UI's **Causal** toggle is on, the agent doesn't just answer — it builds a **causal graph** of the problem, **formally identifies** any treatment effect with DoWhy (and estimates it from data when a dataset is present), derives a **plan** along the critical path, **executes** it step-by-step with code, **propagates the impact** of any failure through the graph, **replans only the affected subgraph**, and finally **synthesizes** a grounded answer. Everything except the five LLM roles is deterministic Python.

This document is the deep-dive. For where the files live, see the [Repository Structure Guide](repository_structure.md); for the surrounding architecture, see the [Developer Guide](developer_guide.md).

---

## 1. Design Principles

1. **Determinism where it counts.** Routing, graph construction/repair, impact propagation, plan derivation, verdict parsing, replan splicing, and **statistical identification/estimation (DoWhy)** are pure Python (`networkx` + pydantic + `dowhy`). LLMs are used only for the five things they're good at: decomposing, naming the estimand variables, executing a step, replanning a subgraph, and writing the final answer.
2. **Bounded cost.** The LLM budget per turn is `1 (decompose) + [≤1 (estimand spec — effect queries only)] + ≤max_steps (execute) + ≤max_replans (replan) + 1 (synthesize)`. The estimand-spec stage is skip-gated (§5), and DoWhy identification/estimation add **0** LLM calls. Budgets are sized per query by complexity and clamped by env ceilings. The loop has a hard structural ceiling (`LOOP_MAX_ITERATIONS=16`).
3. **Vertex tool isolation.** Vertex rejects mixing built-in tools (code execution) with function declarations, so **no `FunctionTool`s** are used. Each `LlmAgent` carries at most one of `{code_executor, output_schema, tools}`; all deterministic work lives in callbacks and custom `BaseAgent`s. This invariant is enforced by `tests/test_causal_agents.py`.
4. **One write, two purposes.** Every deterministic step writes to ADK session state via `actions.state_delta`. That single write is simultaneously the **persistence** record and the **UI transport** the proxy reads — no separate reporting channel.

---

## 2. Agent Tree

Built by `build_root_agent` / `build_causal_pipeline` in [`src/causal/agents.py`](../src/causal/agents.py):

```text
CausalRouterAgent  (custom, 0 LLM)         ── marker routing + state reset + budgets
├── general_assistant                       ── non-causal messages
└── CausalPipeline  (SequentialAgent)
    ├── CausalDecomposer  (LlmAgent, 1)     ── output_schema=CausalDecomposition
    │     └─ after: build_graph_and_plan     ── DAG + plan (deterministic)
    ├── CausalEstimandSpec (LlmAgent, ≤1)   ── output_schema=CausalEstimand; skip-gated to effect queries
    ├── CausalEstimator   (custom, 0 LLM)   ── DoWhy identify (+ estimate/refute/counterfactual if data)
    ├── CausalExecutorLoop  (LoopAgent, ≤16)
    │     ├── CausalStepExecutor (LlmAgent) ── one step, BuiltInCodeExecutor
    │     ├── CausalStepController (custom) ── verdict, ledger, impact, replan-req
    │     └── CausalReplanner   (LlmAgent)  ── output_schema=ReplanResult (skipped on happy path)
    │           └─ after: splice_replan
    ├── CausalSynthesizer  (LlmAgent, 1)    ── output_key=causal_final_answer
    └── CausalFallbackEmitter (custom, 0)   ── fenced causal-json only if CAUSAL_TEXT_FALLBACK=1
```

| Agent | LLM calls | Responsibility |
|---|---|---|
| `CausalRouterAgent` | 0 | Routes by the `[[causal:on]]` marker; on a causal turn, resets stale `causal_*` state and seeds complexity-sized budgets. |
| `CausalDecomposer` | 1 | Emits `CausalDecomposition` (components + directed causal edges) via constrained decoding. |
| `CausalEstimandSpec` | ≤1 | On effect queries only, emits a **variable-level** DAG + treatment/outcome (`CausalEstimand`); skip-gated otherwise (§5). |
| `CausalEstimator` | 0 | Deterministic DoWhy identification (always) + estimation/refutation and gcm counterfactuals (only with a dataset). See §5. |
| `CausalStepExecutor` | 1/step | Executes exactly one plan step, using Python where it helps; ends with an `OBSERVED:`/`STEP_STATUS:` trailer. |
| `CausalStepController` | 0 | The deterministic heart — see §6. |
| `CausalReplanner` | ≤1/failure | Produces replacement steps **only** for the affected subgraph; skipped unless a replan was requested. |
| `CausalSynthesizer` | 1 | Writes the final user-facing answer grounded in the executed plan. |
| `CausalFallbackEmitter` | 0 | Optional text transport for proxies that can't read state deltas. |

### End-to-end execution flow

One turn, start to finish. **Orange** nodes are the (up to five) LLM calls; **green** nodes are deterministic Python (zero LLM); **diamonds** are deterministic gates. The happy path is the straight line down the middle — the branches are the skip-gates, the DoWhy stage, and the failure/replan loop. Dotted edges show the identified estimand *grounding* the executor and synthesizer prompts.

```mermaid
flowchart TD
    U(["User message"]) --> RT{"Contains<br/>&#91;&#91;causal:on&#93;&#93; ?"}
    RT -->|no| GA["general_assistant<br/>LLM + code executor"]
    GA --> OUT(["Response to user"])

    RT -->|yes| RST["CausalRouterAgent · 0 LLM<br/>reset causal_* · complexity → budgets<br/>store causal_query"]
    RST --> DEC["CausalDecomposer · LLM<br/>→ CausalDecomposition"]
    DEC --> BGP["build_graph_and_plan · 0 LLM<br/>DAG build / repair · critical path · plan"]
    BGP --> DECOK{"decomposition<br/>parseable?"}
    DECOK -->|yes| EG{"is_effect_query?"}
    EG -->|yes| ESP["CausalEstimandSpec · LLM<br/>→ CausalEstimand · variable-level DAG"]

    subgraph ESTG["CausalEstimator · 0 LLM · DoWhy"]
        direction TB
        IDN["identify_effect · data-free<br/>back-door / IV adjustment set"]
        IDN --> DATA{"dataset in<br/>message?"}
        DATA -->|no| WE1["write causal_estimand"]
        DATA -->|yes| EM["estimate_effect + refute<br/>(+ gcm counterfactual if asked)"]
        EM --> WE2["write causal_estimand + causal_effect<br/>(+ causal_counterfactual)"]
    end

    subgraph LOOP["CausalExecutorLoop · LoopAgent · ≤16 iterations"]
        direction TB
        RDY{"next ready<br/>step?"}
        RDY -->|yes| EXE["CausalStepExecutor · LLM<br/>code execution + estimand grounding<br/>ends OBSERVED: / STEP_STATUS:"]
        EXE --> CTL["CausalStepController · 0 LLM<br/>parse verdict · ledger · graph"]
        CTL --> VD{"verdict?"}
        VD -->|success| DN{"plan done or<br/>budget spent?"}
        DN -->|more steps| RDY
        VD -->|failure / deviation| PROP["propagate_impact → invalidate<br/>affected subgraph"]
        PROP --> RPB{"replan<br/>budget?"}
        RPB -->|yes| RPL["CausalReplanner · LLM<br/>→ ReplanResult · affected only"]
        RPL --> SPL["splice_replan · 0 LLM<br/>insert steps · bump plan version"]
        SPL --> RDY
        RDY -->|none / deadlock| EXIT(["exit loop"])
        DN -->|done| EXIT
        RPB -->|no| EXIT
    end

    ESP --> IDN
    EG -->|no| RDY
    WE1 --> RDY
    WE2 --> RDY
    DECOK -->|no| SYN
    EXIT --> SYN["CausalSynthesizer · LLM<br/>final answer + estimand grounding<br/>→ causal_final_answer"]
    SYN --> FB["CausalFallbackEmitter · 0 LLM<br/>fenced causal-json · opt-in"]
    FB --> OUT

    IDN -. grounds .-> EXE
    IDN -. grounds .-> SYN

    classDef llm fill:#fde3c4,stroke:#e08a2e,color:#1a1a1a;
    classDef det fill:#d6ebd4,stroke:#4e9a4e,color:#1a1a1a;
    class GA,DEC,ESP,EXE,RPL,SYN llm;
    class RST,BGP,IDN,EM,WE1,WE2,CTL,PROP,SPL,FB det;
```

Reading it: the five LLM calls (orange) are `decompose → estimand-spec → execute-step → replan → synthesize`; everything else — routing, graph build/repair, DoWhy identification & estimation, verdict/impact/replan bookkeeping, transport — is deterministic. Both skip-gates (`is_effect_query`, `next ready step?`) and the replanner's own guard keep the common path cheap.

---

## 3. Routing & Budgets

[`src/causal/router.py`](../src/causal/router.py) decides the pathway with zero LLM calls: an LLM router would need auto-injected transfer declarations (which Vertex won't mix with code execution) and cost a call per turn. Marker matching is free.

On a causal turn the router:
1. Resets every `causal_*` key (`ALL_KEYS`) to clear stale state from a prior turn in the session.
2. Computes a **complexity tier** and a **budget** from the query text.
3. Seeds `causal_budgets`, an initial `causal_steps` trace line, an empty ledger, and `CausalStatus(phase="decomposing")`.

Complexity scoring ([`src/causal/complexity.py`](../src/causal/complexity.py)) is deterministic and cheap — it sizes the reasoning budget *before* spending any tokens:

| Tier | max_steps | max_replans |
|---|---|---|
| simple | 3 | 1 |
| moderate | 5 | 1 |
| complex | 8 | 2 |
| very_complex | 12 | 2 |

The score comes from lexical signals: length buckets, causal/analytical vocabulary, comparison/scenario framing, multiple question marks, and clause density. Attached-file blocks are stripped before scoring so a large pasted file doesn't inflate complexity. The per-query budget is then **clamped** by the `CAUSAL_MAX_STEPS` / `CAUSAL_MAX_REPLANS` env ceilings (defaults 8 / 2).

---

## 4. The Graph Engine

[`src/causal/graph_engine.py`](../src/causal/graph_engine.py) — `CausalTaskGraph`, a validated DAG plus pure operations on it (no I/O, no LLM).

### Build & repair
`from_decomposition` turns the LLM's `CausalDecomposition` into a valid DAG, repairing deterministically (never a second LLM call):
- de-duplicate components and edges, drop self-loops and dangling edges;
- cap components at `MAX_COMPONENTS` (12, keeping outcomes + ancestors first) and edges at `MAX_EDGES` (24, keeping the highest-confidence);
- break cycles by removing the lowest-confidence edge of each cycle until the graph is acyclic.

All repairs are recorded in `repair_notes` and surfaced as `[graph] repair: …` trace lines.

### Critical path & plan
`critical_path` is the confidence-weighted longest path into the outcome nodes (the "global optimum pathway"). `derive_plan` produces one `PlanStep` per **actionable** component (`process`/`artifact`/`outcome`) in topological order; step dependencies mirror causal in-edges. If the plan exceeds `max_steps`, truncation keeps the critical path first.

### Impact propagation & replanning
- `propagate_impact(component_id)` → the descendants a change affects (`nx.descendants`).
- `invalidate(plan, affected)` marks steps on affected components `invalidated`; completed work on **unaffected** components is left intact, keeping replans localized.
- `subgraph_slice(...)` extracts just the affected components/edges to feed the replanner (never the whole graph).
- `splice(plan, replan, request)` inserts replacement steps (rejecting out-of-scope ones deterministically) and bumps the plan version.

---

## 5. Formal Identification & Estimation (DoWhy)

Structural graph reasoning (the component DAG in §4) is not statistical causal inference. For **treatment-effect** questions the pipeline adds a deterministic DoWhy stage so *identification* — deciding which variables to adjust for — is a graph algorithm, not the LLM's guess (LLMs quietly over-adjust for mediators/colliders, biasing the estimand).

Two agents, inserted between the decomposer and the executor loop:

- **`CausalEstimandSpec`** — a schema-only `LlmAgent` that emits a **variable-level** DAG (`CausalEstimand`: variables with roles + directed edges + which is treatment/outcome, plus optional counterfactual anchor values). This is a different abstraction from the decomposer's *task* graph, so it gets its own shallow schema. A before-callback (`skip_unless_effect_query`) returns `_SKIP` unless the query is an effect-estimation ask — detected by the lexical `complexity.is_effect_query` **OR-ed with the decomposer's semantic `is_effect_query` flag** (the decomposer is already a structured call, so paraphrases the regex misses still get the formal path at zero extra cost). When a dataset is attached, its column headers are pinned into the prompt (`estimation.dataset_headers`) so the LLM's variable ids line up with the columns estimation will need.
- **`CausalEstimator`** ([`estimator.py`](../src/causal/estimator.py)) — a deterministic `BaseAgent` (0 LLM) that runs [`estimation.run_identification`](../src/causal/estimation.py):
  - **Identification (always, data-free):** builds a DoWhy `CausalModel` from the variable DAG and calls `identify_effect` → the back-door adjustment set / instruments, the estimand type, and whether the effect is identifiable at all. This needs no dataset because identification is purely symbolic — which is why it fits a project whose queries usually carry no data. Variables declared in the graph but absent from the data are treated by DoWhy as **unobserved** confounders, correctly forcing IV (or honest non-identifiability) instead of a biased back-door estimate.
  - **Estimation + refutation (only with data):** if a CSV/TSV was attached (`parse_dataset` extracts it from the `--- Attached file ---` block the proxy injects), it runs `estimate_effect` — the method matched to the identified estimand (back-door linear regression / `iv.instrumental_variable` / `frontdoor.two_stage_regression`) — plus two refuters (`random_common_cause`, `placebo_treatment_refuter`). Refutation verdicts use the refuter's own **significance test (p > 0.05 ⇒ survived)** when DoWhy provides one; a 15% tolerance is only the fallback.
  - **Counterfactuals (rung 3, data + counterfactual phrasing only):** for "what would Y have been had T…" queries (`complexity.is_counterfactual_query`), `estimation.run_counterfactual` fits a full SCM with `dowhy.gcm` and compares average outcomes under `do(T=baseline)` vs `do(T=intervention)` — anchor values come from the query when named, else the treatment's 25th/75th percentiles.

All results ride one `state_delta` (`causal_estimand` / `causal_effect` / `causal_counterfactual`; one write, two purposes). The identified estimand — and the counterfactual contrast when computed — is injected into the executor and synthesizer prompts (`prompts._estimand_grounding`), so the LLM's numeric answer is anchored to the formal adjustment set instead of ad-hoc confounders — identification moves *out* of the LLM, the same "determinism where it counts" principle as the rest of the engine.

**Never fatal:** any DoWhy or dataset problem degrades to a noted, not-identifiable `IdentificationResult` (counterfactuals simply don't emit); the pipeline keeps running and the LLM still answers. Requires `dowhy>=0.12` (0.11.x is incompatible with `networkx>=3.3`).

---

## 6. The Execution Loop

The `LoopAgent` runs three sub-agents per iteration: **executor → controller → replanner**. The controller ([`src/causal/controller.py`](../src/causal/controller.py)) is where the determinism lives — it runs after the executor on every iteration and yields exactly one event whose `state_delta` is both the persistence write and the UI transport.

Each iteration:
1. **Read the verdict.** `parse_step_verdict` ([`runtime.py`](../src/causal/runtime.py)) parses the executor's `OBSERVED:` / `STEP_STATUS:` trailer. A missing trailer is a `deviation`; a failed code-execution outcome downgrades a claimed `success` to a `deviation`.
2. **On success** — mark the step/component done, append a `[ok]` trace line, then either advance to the next ready step, stop if the step budget is spent, or move to `synthesizing` if the plan is complete.
3. **On failure/deviation** — mark the step/component failed, `propagate_impact` to descendants, `invalidate` the affected steps, and if replan budget remains, build a `ReplanRequest` for the affected subgraph (→ the replanner runs next iteration); otherwise escalate with `budget_exhausted`.
4. **Record** a `ChangeRecord` in the append-only, capped [ledger](../src/causal/ledger.py) and write the updated plan, graph (full + UI shape), trace, and status.

Guard callbacks ([`src/causal/callbacks.py`](../src/causal/callbacks.py)) keep the happy path cheap: `skip_if_no_ready_step` prevents an LLM call when there's nothing to execute, and `skip_unless_replan_requested` means replanning costs **0** LLM calls unless a failure actually requested it. `skip_if_aborted` short-circuits the whole loop when decomposition failed.

If decomposition is unparseable, the pipeline degrades gracefully: it sets `phase="failed"` with a note, and the synthesizer answers the question directly without the graph.

---

## 7. State-Key Contract & Transport

All pipeline state lives under `causal_*` session keys ([`src/causal/state_keys.py`](../src/causal/state_keys.py)):

| Key | Contents | Read by the proxy? |
|---|---|---|
| `causal_graph` | UI-shaped `{nodes, edges, critical_path, version}` | ✅ → `causal_graph` |
| `causal_steps` | `list[str]` human-readable trace lines | ✅ → `causal_reasoning_steps` |
| `causal_status` | `CausalStatus` (phase + counters) | ✅ → `causal_status` |
| `causal_final_answer` | synthesizer's answer | ✅ → `response` |
| `causal_graph_full` | full `CausalGraph` for rehydration | internal |
| `causal_estimand` | `IdentificationResult` (adjustment set, estimand type, identifiability) | ✅ → `causal_estimand` |
| `causal_effect` | `EffectEstimate` (point, CI, refutations + p-values) or null | ✅ → `causal_effect` |
| `causal_counterfactual` | `CounterfactualResult` (do-contrast outcomes + delta) or null | ✅ → `causal_counterfactual` |
| `causal_plan`, `causal_ledger`, `causal_current_step`, `causal_budgets`, `causal_estimand_spec_raw`, … | pipeline internals | internal |

The proxy collects every `causal_*` key it sees in each event's `actions.state_delta` and returns the UI-facing fields. Because the marker and the `causal_` prefix are the only knowledge shared between the two backends, the proxy duplicates just those two constants (it does not ship `src/`).

**Transport fallback.** If a proxy can't read state deltas, running the agent with `CAUSAL_TEXT_FALLBACK=1` makes `CausalFallbackEmitter` emit the results as a fenced ` ```causal-json ` block; the proxy's `_extract_causal_fallback` parses it and strips it from the visible answer. This path is silent (zero LLM calls) by default.

---

## 8. Rendering in the UI

[`proxy/static/causal-agent.js`](../proxy/static/causal-agent.js) turns the payload into the **Causal reasoning** panel: a phase badge, a **Formal identification card**, the step trace (tagged `[ok]`/`[FAIL]`/etc.), and a **Mermaid flowchart** of the graph. Node status maps to colors (pending/active/done/failed/affected), critical-path edges are thickened, and `informs`/`constrains` relations render dashed. Because node ids and labels come from the LLM, they are treated as untrusted and whitelist-sanitized before Mermaid renders (which itself runs with `securityLevel: "strict"`).

The identification card (`buildEstimandCard`) shows the estimand-type chip (backdoor/iv/frontdoor, amber when not identifiable), `treatment → outcome`, the adjustment-set pills, and — when a dataset produced numbers — the effect ± CI, method, n, pass/fail refutation badges (p-value in the tooltip), and the counterfactual do-contrast. Everything renders through `textContent` (never `innerHTML`), so LLM/DoWhy strings stay inert.

In the proxy's mock mode (no `AGENT_ENGINE_ENDPOINT`), a canned 3-node graph, step trace, and identification card payload are returned so the entire causal UI is developable offline.

---

## 9. Environment Variables

| Variable | Default | Effect |
|---|---|---|
| `CAUSAL_MAX_STEPS` | 8 | Ceiling on executed plan steps per turn (clamps the dynamic budget). |
| `CAUSAL_MAX_REPLANS` | 2 | Ceiling on localized replans per turn. |
| `CAUSAL_TEXT_FALLBACK` | off | When `1`, also emit results as a fenced `causal-json` block. |

Structural constants (not env-overridable) live in `state_keys.py`: `MAX_COMPONENTS=12`, `MAX_EDGES=24`, `LOOP_MAX_ITERATIONS=16`, `LEDGER_CAP=50`.

---

## 10. Tests

| File | Covers |
|---|---|
| `tests/test_causal_agents.py` | Agent-tree wiring and the built-in-tool isolation invariant. |
| `tests/test_causal_estimation.py` | DoWhy identification correctness (confounder/mediator/collider), effect recovery + refutation, gcm counterfactuals, dataset/header parsing, and the effect/counterfactual query gates. |
| `tests/test_causal_benchmark.py` | Ground-truth benchmark: synthetic SCMs with known ATEs (confounders, mediator/collider traps, irrelevant covariate, unobserved confounder → IV) across seeds. |
| `tests/test_causal_complexity.py` | Complexity scoring → budget tiers. |
| `tests/test_causal_engine.py` | Graph build/repair, critical path, plan derivation, impact, splice. |
| `tests/test_causal_runtime.py` | Verdict parsing and decision helpers. |
| `tests/test_causal_pipeline_flow.py` | End-to-end flow over the engine. |
| `tests/test_main_causal.py` | Proxy transport: marker prepend, `state_delta` collection, fenced-block fallback, mock graph. |

The pure modules (`state_keys`, `models`, `complexity`, `graph_engine`, `runtime`, `ledger`, `estimation`) have no ADK/Vertex imports, so these tests run fast and hermetically. `estimation` lazily imports `dowhy`/`pandas` inside its functions, so importing it stays cheap; the DoWhy correctness tests `importorskip("dowhy")` and are the only ones that need the heavy dependency.

**LLM-in-the-loop tier.** `tests/eval/datasets/causal-inference-dataset.json` runs the same ground truths *end-to-end through the deployed pipeline* (marker included in the prompts): back-door identification with and without data, the mediator/collider traps, and a counterfactual — graded by the LLM judge in `tests/eval/metrics.py`, whose `causal_correctness` axis is weighted highest. Run with `agents-cli eval generate --dataset tests/eval/datasets/causal-inference-dataset.json` then `agents-cli eval grade`.
