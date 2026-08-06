# Local Development with a Real Vertex AI Agent

Running the full stack locally — the ADK agent **and** the proxy — against real
Vertex AI, authenticated with Application Default Credentials.

If you only need to work on the UI or the proxy, don't do this. Use the offline
mock path instead:

```bash
MODE=mock docker compose up --build     # http://localhost:8080
```

That needs no GCP credentials and costs nothing. The rest of this document is for
when you specifically need the real agent in the loop.

---

## 1. Authenticate (ADC)

```bash
gcloud auth application-default login
```

This writes a credential file — `~/.config/gcloud/application_default_credentials.json`
on macOS/Linux, `%APPDATA%\gcloud\` on Windows. The compose files mount it into
the container.

## 2. Configure `.env`

```env
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_REGION=europe-west2
GOOGLE_GENAI_USE_VERTEXAI=true
```

> [!IMPORTANT]
> Use **`europe-west2`**, not `us-central1`. The project's global Gemini quota is
> exhausted while the regional endpoint is healthy, and `src/agent.py` rewrites
> `GOOGLE_CLOUD_LOCATION=global` to this region on import. `docker-compose.dev.yml`
> forces it too. A stale `us-central1` will fail in confusing ways.

Remove or comment out `GEMINI_API_KEY` to avoid conflicts.

## 3. Run the agent

`docker-compose.dev.yml` runs the agent on **8080** with hot-reload: it mounts
`./src`, maps your ADC file in, runs as root to avoid host permission errors on
the credential file, and sets `PYTHONPATH` so Python finds the builder-user's
dependencies.

```bash
docker compose -f docker-compose.dev.yml up tracerlensai-app --build
```

Naming the service matters — a bare `up` also starts the file's own `proxy`
service on 8081, which is not what you want if you intend to run the proxy
yourself in step 4.

Keep this terminal open.

## 4. Run the proxy against it

The proxy serves the UI and forwards chat to the agent. **Build the UI first** —
it serves `ui/dist`, not source, and returns 503 without it.

```bash
cd ui && npm ci && npm run build && cd ..

export AGENT_ENGINE_ENDPOINT="http://127.0.0.1:8080/api/stream_reasoning_engine"
export ACCESS_STORE=memory          # no Firestore credentials needed
export ADMIN_TOKEN=local-admin      # else /admin returns 503
export APP_URL=http://localhost:8001 # else the app refuses to boot

uvicorn proxy.main:app --host 0.0.0.0 --port 8001 --reload
```

Every one of those variables is required. The access gate reads a record on
every request, so without `ACCESS_STORE=memory` the proxy needs real Firestore
credentials just to open the chat.

Alternatively, skip this step and use the compose file's own `proxy` service on
**8081**, which is already configured:

```bash
docker compose -f docker-compose.dev.yml up proxy --build
```

## 5. Open it

**<http://localhost:8001>** (or 8081 for the compose proxy).

Sign in with any email — in `ACCESS_STORE=memory` mode the record is created
locally and `docker/local-entrypoint.sh` seeds a local admin.

### Troubleshooting

- **Empty response / `b''`** — the agent crashed mid-stream (often
  `SessionNotFoundError`). Check the agent's compose logs.
- **503 on page load** — `ui/dist` is missing. Run `npm run build` in `ui/`.
- **403 on `/analyze-prompt`** — you aren't signed in. The gate rejects
  sessionless callers.
- **App won't start** — `APP_URL` is unset, or set to a localhost value without
  `ALLOW_LOCALHOST_APP_URL`.
- **404 from the agent** — `AGENT_ENGINE_ENDPOINT` must match
  `/api/stream_reasoning_engine` exactly, with no trailing colon.
- **Port in use** — `fuser -k 8001/tcp` on Linux/WSL;
  `Get-NetTCPConnection -LocalPort 8001` then `Stop-Process` on Windows.

---

## 6. Exercising the causal pathway

Flip the **Causal** toggle in the sidebar and send a prompt, or drive the
endpoint directly. Note it streams **Server-Sent Events**, not JSON, and requires
a session token:

```bash
TOKEN=...   # from the browser's stored session after signing in

curl -N localhost:8001/analyze-prompt \
  -H "content-type: application/json" \
  -H "authorization: Bearer $TOKEN" \
  -d '{"prompt": "If I raise prices 10%, what happens to revenue given elastic demand?", "causal_reasoning": true}'
```

You'll see `progress` frames as the pipeline advances, `graph` frames as the DAG
fills in, and a final `done` frame carrying `causal_reasoning_steps`,
`causal_graph`, `causal_status`, `causal_estimand`, and a `response` holding only
the synthesizer's answer. The frame contract is specified in the
[Developer Guide](developer_guide.md#the-sse-contract).

To exercise estimation with real numbers, attach
[`tests/fixtures/sales.csv`](../tests/fixtures/sales.csv) — 150 rows from a known
structural model whose true ATE is **−3.00**, so you can check the estimate
against a real answer.

---

## See Also

- [Developer Guide](developer_guide.md) — architecture and the SSE contract
- [Causal Reasoning](causal_reasoning.md) — what the pipeline actually does
- [Access Control](access_control.md) — why the gate needs those env vars
