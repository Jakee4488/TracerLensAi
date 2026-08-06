# TracerLensAi — project context

## What this is

A Gemini chat app with a deterministic causal-reasoning pipeline. Three
deployables:

- **`proxy/`** — FastAPI gateway on Cloud Run. The only thing the browser talks
  to. Holds the email access gate, token quota, history, uploads, and `/admin`.
- **`src/`** — ADK agent on Vertex AI Agent Runtime. Runs the router, the general
  assistant, and the causal pipeline. Never reachable from the browser.
- **`ui/`** — React 18 + TypeScript + Vite, compiled to `ui/dist`, served by the
  proxy.

## Invariants — breaking these is the usual way things go wrong

1. **Vertex tool isolation.** Every `LlmAgent` may carry at most one of
   `{code_executor, output_schema, tools}`, and none may carry `sub_agents`.
   Vertex rejects built-in tools mixed with function declarations. All
   deterministic work lives in callbacks and custom `BaseAgent`s — never in a
   `FunctionTool`. Enforced by `tests/test_causal_agents.py`.
2. **The two backends stay separate.** `proxy/` and `src/` are deployed
   independently and share only: the `[[causal:on]]` / `[[web:on]]` /
   `[[run:<id>]]` message markers, the `causal_` state-key prefix, and the agent
   names in `STAGE_BY_AUTHOR`. The proxy does not import `src/`. Anything else
   crossing that line is a bug.
3. **`/analyze-prompt` streams Server-Sent Events**, not JSON. Frames are
   `progress`, `graph`, `done`, `error`. See `docs/developer_guide.md`.
4. **The proxy serves `ui/dist`, never `ui/src`.** Run `npm run build` in `ui/`
   before anything that loads the frontend. There is no `proxy/static/`.
5. **No user content in logs.** Telemetry runs in `NO_CONTENT` mode on purpose —
   prompts routinely carry attached CSVs and business context, and the privacy
   notice promises no prompt text is retained.
6. **Region is `europe-west2`.** The project's global Gemini quota is exhausted
   while the regional endpoint is healthy.

## Commands

```bash
# What CI runs
python -m pytest tests/ --ignore=tests/ui_tests -v
cd ui && npm run lint && npm run typecheck && npm run build
uv lock --check

# Run it offline — MODE defaults to `real` and costs money without this
MODE=mock docker compose up --build

# Browser E2E (not run by CI; needs a built bundle)
python -m pytest tests/ui_tests -v
```

## Where things live

`docs/README.md` is the documentation index. `docs/repository_structure.md` is
the file-by-file map. `docs/causal_reasoning.md` covers the pipeline.

<!-- mermaid-ai-skills:start -->
## Mermaid Diagrams

When the user asks to create, edit, or visualize a diagram, follow the
instructions in `.github/instructions/mermaid.instructions.md`.
<!-- mermaid-ai-skills:end -->
