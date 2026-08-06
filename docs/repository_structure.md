# Repository Structure

Directory by directory, file by file. If you're looking for where something
lives, this is the map.

The repository holds **two independently deployed backends** plus a frontend:

- **`proxy/`** — the FastAPI gateway on Cloud Run. The only thing the browser talks to.
- **`src/`** — the ADK agent on Vertex AI Agent Runtime. Never reachable from the browser.
- **`ui/`** — React + TypeScript + Vite, compiled to `ui/dist` and served by the proxy.

They share a deliberately small contract: the `[[causal:on]]`, `[[web:on]]`, and
`[[run:<id>]]` message markers, the `causal_` session-state key prefix, and the
agent names in `STAGE_BY_AUTHOR`. Anything else crossing that boundary is a bug.

---

## Top-Level Layout

```text
proxy/                  # Cloud Run gateway  (see below)
src/                    # ADK agent          (see below)
ui/                     # React frontend     (see below)
tests/                  # pytest + Playwright + eval harness
terraform/              # GCP infrastructure as code
docs/                   # this documentation
docker/                 # container entrypoints
.github/                # CI/CD workflows and AI-assistant instructions
```

---

## `proxy/` — Cloud Run Gateway

Everything the browser touches. Roughly 3,200 lines, and the largest single
component in the repo — the access tier is most of it.

| File | Lines | Responsibility |
|---|---|---|
| `main.py` | ~1360 | ASGI app: serves `ui/dist`, auth/history/upload endpoints, and `POST /analyze-prompt`, which proxies the Agent Engine run to the browser as **Server-Sent Events**. Also holds the offline mock stream. |
| `access.py` | ~1030 | The email access gate: address validation, HMAC session/link signing, Firestore records, per-user token quota, run metrics, SMTP/Resend mail. |
| `admin.py` | ~680 | `/admin` — password + emailed OTP two-factor, review endpoints, one-click approve links, the retention sweep, and the server-rendered dashboard HTML. |
| `memstore.py` | ~120 | In-memory stand-in for Firestore, enabled by `ACCESS_STORE=memory`. Offline dev only. Also used by the test suite as its Firestore fake. |

There is **no `proxy/static/`**. The compiled frontend lives in `ui/dist`
(gitignored, built by `Dockerfile.proxy` or `npm run build`), and `proxy/main.py`
resolves it via `UI_DIST` or the default `ui/dist`.

---

## `src/` — ADK Agent

Deployed to Vertex AI Agent Runtime by `agents-cli`. Never serves the browser.

| File | Lines | Responsibility |
|---|---|---|
| `agent.py` | ~130 | Builds the general assistant (Gemini + `BuiltInCodeExecutor`), the root router, and the `AdkApp` / engine wrappers. |
| `fast_api_app.py` | ~115 | The agent-side ASGI entrypoint the container runs: ADK web routes, A2A, reasoning-engine shim, `/feedback`. |
| `_eval_compat.py` | ~55 | Patches the Vertex SDK to tolerate callable instruction providers, which this pipeline uses throughout. Still required. |
| `__init__.py` | ~20 | Lazy (PEP 562) re-exports of `root_agent`, `adk_app`, `engine`, `fastapi_app` for ADK/`agents-cli` discovery. |

### `src/app_utils/` — serving surfaces

| File | Responsibility |
|---|---|
| `services.py` | Process-wide session and artifact services. |
| `a2a.py` | Agent2Agent JSON-RPC routes and the agent card. |
| `reasoning_engine_adapter.py` | `/api/reasoning_engine` and `/api/stream_reasoning_engine` for the Vertex console. |
| `telemetry.py` | GenAI logging setup and the Agent Engine tracer provider. Runs in `NO_CONTENT` mode deliberately — prompts routinely carry user data. |
| `typing.py` | The `Feedback` model. |

### `src/causal/` — the causal-reasoning pipeline

The deep-dive is [causal_reasoning.md](causal_reasoning.md); this is just the map.

| File | Responsibility |
|---|---|
| `agents.py` | `build_causal_pipeline()` / `build_root_agent()`, plus the custom web-ingest and fallback agents. |
| `router.py` | `CausalRouterAgent` — deterministic marker routing, state reset, budget seeding. |
| `prompts.py` | Instruction providers (callables) for every `LlmAgent`. |
| `callbacks.py` | Deterministic glue between the LLM agents and the engine: skip-gates, graph/plan build, replan splice. |
| `controller.py` | `CausalStepController` — the verdict/propagate/replan loop node. |
| `graph_engine.py` | `CausalTaskGraph`: DAG validation and repair, plan derivation, impact propagation, splice. |
| `estimation.py` | Pure DoWhy library — dataset parsing, GML build, identification, estimation, refutation, counterfactuals. **No ADK imports.** |
| `estimator.py` | The ADK `BaseAgent` node that *calls* `estimation.py` and writes the state delta. |
| `discovery.py` | causal-learn (PC + DirectLiNGAM) DAG correction against real data. |
| `complexity.py` | Lexical complexity scoring → per-query budgets; effect/counterfactual query gates. |
| `models.py` | Every pydantic model, plus `slugify` and `parse_model`. |
| `ledger.py` | Capped append-only change ledger and replan-event log. |
| `runtime.py` | Pure decision helpers and the UI trace-line formatters. |
| `state_keys.py` | Markers, session-state key names, budget and cap constants. |

> `estimation.py` and `estimator.py` are **not** duplicates and both are live.
> The first is a pure statistics library; the second is the agent node that
> drives it. The similar names are the only thing they share.

---

## `ui/` — React + Vite Frontend (source)

Compiled to `ui/dist`. The proxy serves the build output, never these files.

```text
ui/
├── index.html          # Bootstraps the app; selects the API base for prod hostnames
├── package.json        # Node dependencies
├── vite.config.ts      # base "./" and build.outDir
├── tsconfig.json
├── eslint.config.js
└── src/
    ├── App.tsx         # Application shell, split-pane layout, run orchestration
    ├── main.tsx
    ├── styles.css
    ├── types.ts        # Report, ChatMessage, CausalGraph, …
    ├── components/
    ├── hooks/
    └── lib/
```

### Components

| File | Responsibility |
|---|---|
| `AccessGate.tsx` | The sign-in / request-access modal and privacy notice. |
| `ChatHeader.tsx` | Hamburger, title, token badge, profile menu or login. |
| `Sidebar.tsx` | Conversation history, model selector, causal and web toggles. |
| `Composer.tsx` | Prompt input, attachments, send / stop. |
| `MessageList.tsx` | Transcript rendering, markdown, live pending bubble. |
| `ProfileMenu.tsx` | Account menu, theme toggle, data deletion. |
| `DropOverlay.tsx` | Drag-and-drop file target. |
| `causal/CausalPanel.tsx` | The right-hand pane. Lazily loaded — it pulls ReactFlow and dagre. |
| `causal/CausalGraph.tsx` | The interactive DAG (ReactFlow + dagre layout). |
| `causal/WorkflowTimeline.tsx` | Stage-by-stage run progress. |
| `causal/EstimandCard.tsx` | Identification result and adjustment set. |
| `causal/EffectChart.tsx` | Effect estimate, CI, and refutation results. |
| `causal/PlanView.tsx` | The execution plan and per-step status. |
| `causal/StepDrawer.tsx` | Click-through detail for a step or node. |

### Hooks and lib

| File | Responsibility |
|---|---|
| `hooks/useAccess.ts` | Access-gate state machine and session polling. |
| `hooks/useHistory.ts` | Conversation list and loading. |
| `hooks/useAttachments.ts` | Upload lifecycle. |
| `hooks/useRunProgress.ts` | Accumulates SSE frames into stages and a live graph. |
| `hooks/useFocusTrap.ts` | Modal focus containment. |
| `hooks/useMediaQuery.ts` | Responsive breakpoints. |
| `lib/api.ts` | Every backend call, plus auth headers. |
| `lib/sse.ts` | SSE frame reader. |
| `lib/stages.ts` | Stage model the timeline renders. |
| `lib/graph.ts` | dagre layout, edge building, topology keys. |
| `lib/markdown.ts` | marked + DOMPurify, syntax highlighting, `[Node: …]` citation linkifying. |
| `lib/causal.ts` | `hasCausalContent` — kept dependency-free so the panel can load lazily. |
| `lib/export.ts` | `downloadRun` — one auditable JSON file per run. |
| `lib/access.ts` | Session token storage. |
| `lib/ids.ts` | Session, run, message, and anonymous ids. |
| `lib/theme.ts` | Light/dark persistence. |

---

## `tests/` — Test Suite

| File | Tests | Covers |
|---|---|---|
| `test_access.py` | 52 | Email gate, sessions, quota, retention, mail. |
| `test_admin.py` | 36 | OTP two-factor, review endpoints, sweep, dashboard. |
| `test_main.py` | 35 | Proxy endpoints, history, uploads, SSE, token accounting. |
| `test_main_causal.py` | 31 | Causal transport end to end through the proxy. |
| `test_causal_estimation.py` | 26 | DoWhy identification, estimation, refutation, counterfactuals. |
| `test_causal_engine.py` | 19 | Graph engine, ledger, impact propagation, splice. |
| `test_causal_runtime.py` | 14 | Decision helpers and trace formatting. |
| `test_causal_agents.py` | 13 | Agent tree shape and the Vertex tool-isolation invariant. |
| `test_causal_complexity.py` | 10 | Complexity tiers and budgets. |
| `test_causal_discovery.py` | 7 | causal-learn DAG correction. |
| `test_causal_pipeline_flow.py` | 5 | Full pipeline wiring. |
| `test_causal_prompts.py` | 3 | Instruction providers render and cite correctly. |
| `test_app_entrypoint.py` | 3 | The production ASGI entrypoint imports and serves. |
| `test_causal_benchmark.py` | 2 | Performance guardrails. |
| `ui_tests/test_ui.py` | 34 | Playwright browser E2E. |
| `ui_tests/test_access_gate.py` | 13 | Playwright access-gate behaviour. |

`tests/fixtures/sales.csv` is the canonical dataset for manual testing — 150 rows
from a known structural model with a true ATE of −3.00, so an estimate can be
checked against a real answer. `tests/eval/` holds the `agents-cli eval`
datasets, config, and custom metrics.

> `conftest.py` uses `proxy/memstore.py` as its Firestore fake rather than
> carrying its own copy. It used to have a duplicate, and the two silently
> diverged.

**CI runs `pytest tests/ --ignore=tests/ui_tests`.** The Playwright suite is not
run by CI and needs a built bundle (`cd ui && npm run build`) plus
`playwright install chromium`.

---

## `terraform/` — Infrastructure as Code

| File | Provisions |
|---|---|
| `main.tf` | Provider, API enablement. |
| `cloudrun.tf` | The `tracerlensai-app` service and public access. |
| `iam.tf` | Service accounts, roles, Workload Identity Federation. |
| `storage.tf` | GCS buckets. |
| `causal_mlops.tf` | BigQuery dataset and causal artifact storage. |
| `variables.tf` / `outputs.tf` | Inputs and outputs. |

---

## `.github/` — CI/CD & Automation

| File | Runs |
|---|---|
| `workflows/ci.yml` | `uv lock --check`, `pytest tests/ --ignore=tests/ui_tests`, and the UI's `lint` / `typecheck` / `build`. |
| `workflows/deploy.yml` | Push to `main` → agent, proxy, and hosting. |
| `workflows/deploy-dev.yml` | Push to any other branch → `tracerlensai-app-dev`, **proxy only**. |
| `workflows/deploy-staging.yml` | Pull request → `tracerlensai-app-staging-pr-<N>`, **proxy only**. |
| `workflows/uptime.yml` | Health probe; writes `badges/uptime.json`. |
| `copilot-instructions.md` | Project context for AI assistants. |
| `instructions/mermaid.instructions.md` | Diagram conventions. |

---

## Root Configuration

| File | Purpose |
|---|---|
| `LICENSE` | **AGPL-3.0.** Network use counts as distribution — see the README's License section. |
| `requirements.txt` | Agent + dev dependencies, including dowhy and causal-learn. |
| `requirements-proxy.txt` | Proxy-image dependencies only. Deliberately excludes the ~450 MB causal stack — `Dockerfile.proxy` installs this one. |
| `requirements-dev.txt` | Playwright and test tooling. |
| `pyproject.toml` / `uv.lock` | Dependency lock; CI enforces they agree. |
| `pytest.ini` | `testpaths = tests`, warning filters, the `logged_out` marker. |
| `Dockerfile` | The agent image. |
| `Dockerfile.proxy` | Multi-stage: Node 20 builds `ui/` → Python packages `proxy/` with `ui/dist`. |
| `docker-compose.yml` | Full local stack. `MODE=mock` for offline. |
| `docker-compose.dev.yml` | Hot-reload agent (8080), proxy (8081), test-runner, Playwright services. |
| `docker/local-entrypoint.sh` | Resolves the agent endpoint and seeds a local admin. |
| `deploy_to_gcp.sh` | The three-stage deploy, run by CI and available manually. |
| `deployment_metadata.json` | Written by `agents-cli`; the source of truth for the Agent Engine endpoint. |
| `agents-cli-manifest.yaml` | Agent deployment manifest (region, session type, entrypoint). |
| `firebase.json` / `.firebaserc` | Hosting config — publishes `ui/dist`, rewrites everything else to Cloud Run. |
| `.env.example` | Template for local configuration. |

`artifacts/` holds eval traces and grade results. It is currently tracked in git.
