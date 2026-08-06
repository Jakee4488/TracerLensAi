# TracerLensAi Documentation

Start here. Everything below is kept in sync with the code — if a document and
the source disagree, the source is right and the document is a bug.

## Orientation

| Document | Read it when |
|---|---|
| [Repository Structure](repository_structure.md) | You need to find something — directory by directory, file by file |
| [Developer Guide](developer_guide.md) | You're changing code — architecture, API reference, the SSE contract, local setup |

## The product

| Document | Read it when |
|---|---|
| [Causal Reasoning](causal_reasoning.md) | You're working on the pipeline — retrieval, decomposition, DoWhy identification/estimation, execution, replanning |
| [Access Control](access_control.md) | You're touching sign-in, quotas, retention, or the `/admin` dashboard |
| [Token Calculation](token_calculation.md) | You need to know how usage is measured and why multi-agent runs compound cost |

## Running and shipping it

| Document | Read it when |
|---|---|
| [Local Development (Vertex AI)](local_development_vertex_agent.md) | You want the full stack running locally against a real agent |
| [Deployment Guide](deployment_guide.md) | You're deploying, provisioning, or debugging an environment |
| [Evaluation & Testing](evaluation_and_testing.md) | You're writing tests or running the eval flywheel |

## Conventions

- **Two backends.** `proxy/` (Cloud Run gateway) and `src/` (ADK agent on Vertex
  AI Agent Runtime) are deployed separately and share only a small, deliberate
  contract: the `[[causal:on]]` / `[[web:on]]` / `[[run:<id>]]` markers, the
  `causal_` state-key prefix, and the agent names in `STAGE_BY_AUTHOR`. Anything
  else crossing that line is a bug.
- **Vertex tool isolation.** Vertex refuses to mix built-in tools (code
  execution, Search) with function declarations, so each `LlmAgent` carries at
  most one of `{code_executor, output_schema, tools}`. `tests/test_causal_agents.py`
  enforces this — it is the constraint most likely to bite a new contributor.
- **The UI is compiled.** The proxy serves `ui/dist`, never `ui/src`. Run
  `npm run build` in `ui/` before anything that loads the frontend, including the
  Playwright suite.
