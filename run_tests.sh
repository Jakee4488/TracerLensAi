#!/bin/bash
# =============================================================================
#  run_tests.sh — Local Docker Development & Test Script
#
#  Builds and runs the TracerLensAi Agent inside Docker for local development and
#  testing.  Uses docker compose with the same Dockerfile as production.
#
#  Usage:
#    ./run_tests.sh             # Full test pipeline (build → lint → test →
#                               #   evaluate → smoke test → teardown)
#    ./run_tests.sh --start     # Start dev server with hot-reload
#    ./run_tests.sh --stop      # Stop the running dev server
#    ./run_tests.sh --build-only  # Build the Docker image only
#    ./run_tests.sh --clean     # Remove containers, images, and volumes
#    ./run_tests.sh --help      # Show this help text
#
#  Prerequisites:
#    - Docker Desktop (with Compose V2 plugin)
#    - .env file in the project root (copy from .env.example)
#
#  The dev server mounts ./src as a volume so code changes are reflected
#  immediately without rebuilding.
# =============================================================================

set -euo pipefail

# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; DIM='\033[2m'; RESET='\033[0m'

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
step()    { echo -e "\n${BOLD}${CYAN}══ $* ${RESET}"; }

COMPOSE_FILE="docker-compose.dev.yml"
PROJECT_NAME="tracerlensai"

# ── Logging ───────────────────────────────────────────────────────────────────
mkdir -p logs
LOG_FILE="logs/dev_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

# ── Helper: run a one-shot command in the test-runner container ───────────────
run_in_container() {
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" \
    run --rm --no-deps test-runner "$@"
}

# ── Helper: ensure .env exists ────────────────────────────────────────────────
ensure_env_file() {
  if [ ! -f ".env" ]; then
    warn "No .env file found."
    if [ -f ".env.example" ]; then
      info "Copying .env.example → .env (fill in your values)."
      cp .env.example .env
    else
      warn "Creating a minimal .env with placeholder values."
      cat > .env <<'ENVEOF'
# TracerLensAi — Local Development Environment Variables
# Copy this file and fill in your values.
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_REGION=us-central1
ENVEOF
    fi
  fi
}

# ── Helper: prerequisite checks ──────────────────────────────────────────────
check_prerequisites() {
  step "Checking prerequisites"

  if ! command -v docker > /dev/null 2>&1; then
    error "Docker is not installed. Install Docker Desktop and retry."
    exit 1
  fi
  success "docker found"

  if ! docker compose version > /dev/null 2>&1; then
    error "Docker Compose V2 plugin not found."
    error "Upgrade Docker Desktop or install the compose plugin."
    exit 1
  fi
  success "docker compose $(docker compose version --short) found"

  if ! docker info > /dev/null 2>&1; then
    error "Docker daemon is not running. Start Docker Desktop and retry."
    exit 1
  fi
  success "Docker daemon is running"
}

# ── Command: BUILD ────────────────────────────────────────────────────────────
cmd_build() {
  step "Building Docker image"
  ensure_env_file
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" build
  success "Docker image built successfully."
}

# ── Command: START (dev server with hot-reload) ───────────────────────────────
cmd_start() {
  step "Starting dev server (hot-reload enabled)"
  ensure_env_file

  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d --build tracerlensai-app
  success "Dev server started on http://localhost:8080"
  info "Source code is mounted — edits to src/ will auto-reload."
  info "Stop with: ./run_tests.sh --stop"
  echo ""

  info "Waiting for health check..."
  local retries=12
  for i in $(seq 1 $retries); do
    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
      success "Health check passed — server is ready."
      return 0
    fi
    sleep 2
  done
  warn "Server started but health check did not pass within ${retries}×2s."
  warn "Check logs: docker compose -f $COMPOSE_FILE -p $PROJECT_NAME logs -f"
}

# ── Command: STOP ─────────────────────────────────────────────────────────────
cmd_stop() {
  step "Stopping dev server"
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down
  success "Dev server stopped."
}

# ── Command: CLEAN ────────────────────────────────────────────────────────────
cmd_clean() {
  step "Cleaning up Docker resources"
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down -v --rmi local --remove-orphans 2>/dev/null || true
  success "All containers, volumes, and locally-built images removed."
}

# ── Command: FULL TEST PIPELINE ──────────────────────────────────────────────
cmd_test_pipeline() {
  echo ""
  echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════╗${RESET}"
  echo -e "${BOLD}${CYAN}║   TracerLensAi — Local Docker Test Pipeline     ║${RESET}"
  echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════╝${RESET}"
  echo ""

  ensure_env_file

  # 1 — Build
  step "1 │ Building Docker image"
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" build
  success "Image built."

  # 2 — Lint
  step "2 │ Running flake8 linting"
  if run_in_container flake8 src/; then
    success "Linting passed."
  else
    error "Linting FAILED — fix the issues above."
    cmd_stop 2>/dev/null || true
    exit 1
  fi

  # 3 — Unit tests
  step "3 │ Running pytest"
  if run_in_container python -m pytest tests/ -v --tb=short; then
    success "All unit tests passed."
  else
    error "Unit tests FAILED — fix the failures above."
    cmd_stop 2>/dev/null || true
    exit 1
  fi

  # 4 — Evaluator harness
  step "4 │ Running synthetic evaluation harness"
  if run_in_container python -m src.observability.evaluator; then
    success "Evaluation harness completed."
  else
    warn "Evaluation harness exited non-zero (may need Vertex AI — non-fatal)."
  fi

  # 5 — Smoke test (start app → hit endpoints → stop)
  step "5 │ Running API smoke tests"
  info "Starting app container..."
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" up -d tracerlensai-app

  info "Waiting for server to become healthy..."
  local healthy=false
  for i in $(seq 1 15); do
    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
      healthy=true
      break
    fi
    sleep 2
  done

  if $healthy; then
    success "Health check passed."

    info "Testing POST /inquire-traced..."
    RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:8080/inquire-traced" \
         -H "Content-Type: application/json" \
         -d '{"user_id": "test-user", "prompt": "Hello!"}')

    if [ "$RESPONSE" = "200" ]; then
      success "/inquire-traced returned HTTP 200."
    elif [ "$RESPONSE" = "500" ]; then
      warn "/inquire-traced returned HTTP 500 (Vertex AI may be unavailable locally — non-fatal)."
    else
      warn "/inquire-traced returned HTTP $RESPONSE."
    fi

    info "Testing POST /analyze-prompt..."
    ANALYZE_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:8080/analyze-prompt" \
         -H "Content-Type: application/json" \
         -d '{"prompt": "Please summarize this deployment plan as concise bullets."}')

    if [ "$ANALYZE_RESPONSE" = "200" ]; then
      success "/analyze-prompt returned HTTP 200."
    else
      error "/analyze-prompt returned HTTP $ANALYZE_RESPONSE."
      cmd_stop 2>/dev/null || true
      exit 1
    fi

    info "Testing POST /optimize-workflow..."
    OPTIMIZE_RESPONSE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://localhost:8080/optimize-workflow" \
         -H "Content-Type: application/json" \
         -d '{
           "original_prompt": "Please fetch user profile for user 123",
           "trace": [
             {"step_type": "user_input", "description": "User Message Received", "tokens": 8, "latency_ms": 0.0},
             {"step_type": "llm_call", "description": "LLM Generation", "tokens": 120, "latency_ms": 850.0},
             {"step_type": "response", "description": "Response Delivered", "tokens": 45, "latency_ms": 0.0}
           ],
           "run_prompt_analysis": true
         }')

    if [ "$OPTIMIZE_RESPONSE" = "200" ]; then
      success "/optimize-workflow returned HTTP 200."
    else
      error "/optimize-workflow returned HTTP $OPTIMIZE_RESPONSE."
      cmd_stop 2>/dev/null || true
      exit 1
    fi
  else
    warn "Server did not become healthy within 30s — skipping API tests."
  fi

  # 6 — Tear down
  step "6 │ Tearing down"
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT_NAME" down
  success "Containers stopped and removed."

  # Done
  echo ""
  echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${RESET}"
  echo -e "${BOLD}${GREEN}║   Local Test Pipeline Completed  ✓          ║${RESET}"
  echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${RESET}"
  info "Log: ${LOG_FILE}"
  echo ""
}

# ── Argument parsing ──────────────────────────────────────────────────────────
show_help() {
  echo ""
  echo -e "${BOLD}TracerLensAi — Local Docker Development Script${RESET}"
  echo ""
  echo "Usage:"
  echo "  ./run_tests.sh               Full test pipeline (build → lint → test → smoke)"
  echo "  ./run_tests.sh --start       Start dev server with hot-reload"
  echo "  ./run_tests.sh --stop        Stop the running dev server"
  echo "  ./run_tests.sh --build-only  Build the Docker image only"
  echo "  ./run_tests.sh --clean       Remove containers, images, and volumes"
  echo "  ./run_tests.sh --help        Show this help text"
  echo ""
  echo "The dev server mounts ./src into the container. Code changes"
  echo "are reflected immediately without rebuilding."
  echo ""
}

main() {
  local action="${1:-test}"

  case "$action" in
    --start|--startl)
      check_prerequisites
      cmd_start
      ;;
    --stop)
      cmd_stop
      ;;
    --build-only)
      check_prerequisites
      cmd_build
      ;;
    --clean)
      cmd_clean
      ;;
    --help|-h)
      show_help
      exit 0
      ;;
    test|"")
      check_prerequisites
      cmd_test_pipeline
      ;;
    *)
      error "Unknown argument: $action"
      show_help
      exit 1
      ;;
  esac
}

main "${1:-}"
