# Developer Guide

This document provides a comprehensive technical reference for the TracerLensAi codebase. It covers the full architecture, every source file, every function, the local development environment, and the deployment pipeline.

---

## 1. Architecture Overview

TracerLensAi is a decoupled, two-tier application:

| Tier | Technology | Location |
|---|---|---|
| **Frontend** | Vanilla HTML/CSS/JS | `src/static/` → Firebase Hosting (CDN) |
| **Backend** | Python FastAPI | `src/main.py` → Cloud Run (serverless) |
| **Database** | SQLite | `/app/data/app.db` (ephemeral per Cloud Run instance) |
| **LLM** | Google Gemini via `google-genai` SDK | Vertex AI (`europe-west2`) |

The frontend communicates with the backend through REST API calls. In production, Firebase Hosting serves static assets and proxies API requests to Cloud Run via rewrite rules.

---

## 2. Directory Layout

```text
TracerLensAi/
├── src/
│   ├── main.py                 # FastAPI app — endpoints, GenAI client, Pydantic models
│   ├── database.py             # SQLite CRUD — chats & messages persistence
│   └── static/
│       ├── index.html          # UI shell — sidebar, chat area, header controls
│       ├── causal-agent.js     # Client-side logic — API calls, rendering, state
│       └── styles.css          # Design system — CSS variables, dark/light mode
│
├── tests/
│   ├── conftest.py             # Pytest fixtures — test client, temp DB setup
│   ├── test_main.py            # API endpoint tests
│   ├── test_database.py        # Database function tests
│   └── ui_tests/
│       └── test_ui.py          # Playwright browser tests
│
├── terraform/
│   ├── main.tf                 # Provider configuration
│   ├── variables.tf            # Input variables (project_id, region, etc.)
│   ├── cloudrun.tf             # Cloud Run service + public IAM
│   ├── iam.tf                  # Service accounts, WIF, CI/CD permissions
│   ├── storage.tf              # Artifact Registry, GCS, BigQuery dataset
│   ├── causal_mlops.tf         # API enablement, Causal MLOps registry + bucket
│   └── outputs.tf              # Terraform outputs
│
├── .github/workflows/
│   ├── ci.yml                  # PR gate (lint + test)
│   └── cd.yml                  # Continuous deployment (build → push → deploy)
│
├── helm/tracerlensai/          # Optional GKE Helm chart
├── Dockerfile                  # Multi-stage build (builder + runtime)
├── docker-compose.dev.yml      # Local dev (hot-reload, test runner, UI tests)
├── requirements.txt            # Python dependencies
├── run_tests.sh                # Docker-based dev/test automation
├── deploy_to_gcp.sh            # Manual GCP deployment fallback
├── firebase.json               # Firebase Hosting config + Cloud Run rewrites
└── .firebaserc                 # Firebase project binding
```

---

## 3. Backend — `src/main.py`

The entire backend is contained in a single FastAPI application file.

### App Lifecycle

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
```
Initializes the SQLite database on application startup by calling `init_db()`.

### GenAI Client

```python
@lru_cache(maxsize=1)
def get_genai_client() -> genai.Client:
```
Creates and caches a single `google-genai` client instance. Supports two authentication modes:
1. **Vertex AI (production)**: Uses Application Default Credentials with `vertexai=True`.
2. **API Key (development)**: If `GEMINI_API_KEY` is set, uses direct API key auth instead.

Environment variables read:
- `GOOGLE_CLOUD_PROJECT` (default: `icarus-agent-26`)
- `GOOGLE_CLOUD_LOCATION` / `GOOGLE_CLOUD_REGION` (default: `us-central1`)
- `GEMINI_API_KEY` (optional, overrides Vertex AI auth)
- `GOOGLE_CREDENTIALS_JSON` (optional, writes to temp file for ADC)

### Pydantic Models

| Model | Fields | Purpose |
|---|---|---|
| `NewChatRequest` | `title: str = "New Chat"` | Request body for `POST /api/chats` |
| `PromptRequest` | `prompt`, `causal_reasoning`, `web_search`, `model_name`, `chat_id` | Request body for `POST /analyze-prompt` |

### API Endpoints

#### `GET /` → `read_root()`
Redirects to `/static/index.html`.

#### `GET /health` → `health_check()`
Returns `{"status": "ok"}`. Used by Docker health checks and Cloud Run readiness probes.

#### `GET /api/chats` → `api_get_chats()`
Returns all chat sessions ordered by `created_at DESC`.

#### `GET /api/chats/{chat_id}` → `api_get_chat(chat_id)`
Returns a single chat with its full message history (including deserialized `causal_steps`).

#### `POST /api/chats` → `api_create_chat(req)`
Creates a new empty chat session with the given title. Returns `{"id": ..., "title": ...}`.

#### `POST /analyze-prompt` → `analyze_prompt(req)`
The main analysis endpoint. This function:
1. Loads conversation history from SQLite if `chat_id` is provided.
2. Builds a full prompt string with context prepended.
3. Calls `client.models.generate_content()` with the selected model.
4. Selects tools based on toggle state:
   - **Web Search ON** → `types.Tool(google_search=types.GoogleSearch())`
   - **Web Search OFF** → `types.Tool(code_execution=types.ToolCodeExecution())`
   - *(Note: Vertex AI does not allow mixing search + code_execution tools)*
5. If `causal_reasoning` is enabled, makes a second Gemini call with a causal inference prompt.
6. Persists both user message and AI response to SQLite.
7. Returns JSON with `response`, `total_token_count`, and optional `causal_reasoning_steps`.

---

## 4. Database Layer — `src/database.py`

A lightweight SQLite persistence layer using the Python `sqlite3` standard library.

### Configuration

| Constant | Value | Description |
|---|---|---|
| `DB_DIR` | `/app/data` | Directory for the database file |
| `DB_PATH` | `/app/data/app.db` | Full path to the SQLite database |

### Schema

**`chats` table:**
| Column | Type | Description |
|---|---|---|
| `id` | TEXT PRIMARY KEY | UUID v4 |
| `title` | TEXT | Chat title (auto-set from first message) |
| `total_tokens` | INTEGER DEFAULT 0 | Running token count for the session |
| `created_at` | DATETIME | Auto-set on creation |

**`messages` table:**
| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | Auto-incrementing ID |
| `chat_id` | TEXT (FK → chats.id) | Parent chat reference |
| `role` | TEXT | `"user"` or `"ai"` |
| `content` | TEXT | Message text |
| `causal_steps` | TEXT (JSON) | Serialized list of causal reasoning steps (nullable) |
| `created_at` | DATETIME | Auto-set on creation |

### Functions

| Function | Signature | Description |
|---|---|---|
| `init_db()` | `() → None` | Creates `DB_DIR` if missing, creates tables if they don't exist |
| `create_chat()` | `(title: str) → str` | Inserts a new chat, returns its UUID |
| `get_chats()` | `() → List[Dict]` | Returns all chats ordered by `created_at DESC` |
| `get_chat()` | `(chat_id: str) → Optional[Dict]` | Returns a chat with its messages; deserializes `causal_steps` from JSON |
| `update_chat_tokens()` | `(chat_id: str, added_tokens: int) → None` | Atomically increments `total_tokens` |
| `update_chat_title()` | `(chat_id: str, new_title: str) → None` | Updates a chat's title |
| `add_message()` | `(chat_id, role, content, causal_steps) → None` | Inserts a message; serializes `causal_steps` to JSON |

---

## 5. Frontend — `src/static/`

### `index.html` — UI Layout

The HTML file defines a two-panel layout:
- **Sidebar** (`<aside class="sidebar">`): App logo, navigation, "New chat" button, recent workflows list.
- **Chat Container** (`<main class="chat-container">`): Header bar with model selector, dark mode toggle, web search toggle; messages area; input pill with send button.

External dependencies loaded via CDN:
- Google Fonts (Inter, Google Sans)
- highlight.js (syntax highlighting in code blocks)
- marked.js (Markdown-to-HTML rendering)
- Firebase SDK (analytics)

### `causal-agent.js` — Client Logic

Global state:
- `sessionTotalTokens: number` — Running token count displayed in the header badge.
- `currentChatId: string | null` — Active chat session UUID.

| Function | Description |
|---|---|
| `loadHistoryList()` | Fetches `GET /api/chats` and rebuilds the sidebar history list |
| `loadChat(chatId)` | Fetches `GET /api/chats/{id}`, renders all messages, restores token count |
| `sendMessage()` | Reads input, creates a chat if needed, calls `POST /analyze-prompt`, renders the AI response as parsed Markdown |
| `scrollToBottom()` | Scrolls the messages area to the latest message |
| `escapeHtml(unsafe)` | Sanitizes user input to prevent XSS (`&`, `<`, `>`, `"`, `'`) |

Event listeners:
- **Enter key** (without Shift) → sends message
- **Send button click** → sends message
- **Dark mode toggle** → adds/removes `light-mode` CSS class on `<body>`
- **Sidebar toggle** → collapses/expands sidebar
- **New chat button** → resets `currentChatId`, clears messages area

### `styles.css` — Design System

The CSS file defines a comprehensive design system using CSS custom properties:
- **Color tokens**: `--bg-main`, `--bg-sidebar`, `--bg-input`, `--text-primary`, `--text-secondary`, `--border`, `--accent`
- **Dark mode** (default): Deep blue-gray palette with teal accents
- **Light mode** (`.light-mode` class): Clean white palette
- **Responsive**: Sidebar collapses on screens `≤ 768px`
- **Animations**: Typing indicator with pulsing dots, smooth transitions on all interactive elements

---

## 6. Local Development Environment

### Docker Compose Services (`docker-compose.dev.yml`)

| Service | Container | Purpose |
|---|---|---|
| `tracerlensai-app` | `tracerlensai-dev` | Main app with hot-reload (`uvicorn --reload`). Mounts `./src` as read-only volume. |
| `test-runner` | `tracerlensai-test` | One-shot test container (profile: `test`). Mounts `./src`, `./tests`, config files. |
| `causal-agent-ui-test` | Playwright image | Browser UI tests (profile: `ui-test`). Depends on healthy `tracerlensai-app`. |

### Health Check

The `tracerlensai-app` service has a built-in health check that polls `http://localhost:8080/health` every 10 seconds.

### Developer Script (`run_tests.sh`)

| Command | Action |
|---|---|
| `./run_tests.sh test` | Build → lint → pytest → smoke test → cleanup |
| `./run_tests.sh --start` | Start hot-reload dev server in background |
| `./run_tests.sh --stop` | Stop and remove dev containers |
| `./run_tests.sh --clean` | Full Docker cleanup (containers, images, volumes) |
| `./run_tests.sh --commit "msg"` | Lint → test → git diff → commit → prompt to push |

---

## 7. Testing

### Test Configuration

- **`pytest.ini`**: Configures pytest with test path and options.
- **`conftest.py`**: Provides two fixtures:
  - `client` — FastAPI `TestClient` instance
  - `setup_test_db` (autouse) — Monkeypatches `DB_PATH` and `DB_DIR` to use a temporary directory, ensuring test isolation.

### Test Files

| File | Tests |
|---|---|
| `test_main.py` | `test_health_check` — verifies `/health` returns 200; `test_create_chat` — verifies chat creation; `test_get_chats` — verifies chat listing |
| `test_database.py` | `test_create_and_get_chat` — round-trip create/read; `test_add_message` — verifies message persistence including causal_steps deserialization |
| `ui_tests/test_ui.py` | Playwright browser test (placeholder) |

---

## 8. Deployment Architecture

### Cloud Run (Primary)

The CD pipeline (`.github/workflows/cd.yml`) deploys to Cloud Run on every push to `main`:
1. Authenticates via Workload Identity Federation (OIDC)
2. Builds and pushes the Docker image to GCR (`gcr.io/icarus-agent-26/tracerlensai-app`)
3. Deploys with `--service-account agent-app-sa@...` for Vertex AI access

### Firebase Hosting (Frontend CDN)

Firebase Hosting serves static files from `src/static/` and proxies unmatched requests to Cloud Run via the `rewrites` block in `firebase.json`.

### GKE (Optional Alternative)

The `cd.yml` workflow supports an optional GKE path triggered by setting `DEFAULT_TARGET=gke` in GitHub repository variables or by manual dispatch.

---

## 9. Terraform Infrastructure

All infrastructure is defined in `terraform/`:

| File | Resources |
|---|---|
| `main.tf` | Google provider config (v5.0+) |
| `variables.tf` | `project_id`, `region`, `causal_artifact_repo_name`, `causal_artifacts_bucket`, `github_repo` |
| `cloudrun.tf` | `google_cloud_run_service`, public IAM (`allUsers` invoker) |
| `iam.tf` | `agent-app-sa` (aiplatform.user, bigquery.dataEditor, logging.logWriter), `github-actions-sa` (owner), WIF pool/provider, GKE artifact reader |
| `storage.tf` | Artifact Registry (`agent-docker-repo`), GCS bucket (`agent-cache`), BigQuery dataset (`agent_orchestrator_logs`) |
| `causal_mlops.tf` | API enablement (Artifact Registry, Cloud Functions, Cloud Run, AI Platform), Causal MLOps registry + GCS bucket |
| `outputs.tf` | `artifact_registry_repo` name |
