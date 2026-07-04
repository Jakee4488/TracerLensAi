#!/bin/bash
# =============================================================================
#  build_deploy.sh — Manual GCP Build & Deploy (Fallback)
#
#  ⚠️  PRIMARY DEPLOYMENT is via GitHub CI/CD (merge to main → staging,
#      git tag → production). Use this script ONLY as an escape hatch
#      when CI/CD is unavailable or for debugging deployment issues.
#
#  Usage:
#    ./build_deploy.sh             # Build → push → deploy (with confirmation)
#    ./build_deploy.sh --dry-run   # Show what would happen, don't execute
#    ./build_deploy.sh --no-push   # Build only, no push or deploy
#    ./build_deploy.sh --no-deploy # Build & push, skip Helm deploy
#    ./build_deploy.sh --help      # Show this help text
#
#  Prerequisites:
#    - docker, gcloud, kubectl, helm, git
#    - .env file in the project root (copy from .env.example)
#    - Authenticated to GCP: gcloud auth login
#
#  Expects tests to have already passed (run ./run_tests.sh first).
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

# ── Flags ─────────────────────────────────────────────────────────────────────
RUN_PUSH=true
RUN_DEPLOY=true
DRY_RUN=false

for arg in "$@"; do
  case $arg in
    --dry-run)   DRY_RUN=true ;;
    --no-push)   RUN_PUSH=false; RUN_DEPLOY=false ;;
    --no-deploy) RUN_DEPLOY=false ;;
    --help|-h)
      head -20 "$0" | grep '^#' | sed 's/^#  \?//'
      exit 0
      ;;
    *)
      error "Unknown argument: $arg"
      exit 1
      ;;
  esac
done

# ── Logging ───────────────────────────────────────────────────────────────────
mkdir -p logs
LOG_FILE="logs/build_deploy_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
info "Log file: $LOG_FILE"

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${YELLOW}╔══════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${YELLOW}║   ⚠️  MANUAL DEPLOY — Fallback Mode                 ║${RESET}"
echo -e "${BOLD}${YELLOW}║                                                      ║${RESET}"
echo -e "${BOLD}${YELLOW}║   Primary deploy path: merge PR → main (CI/CD)       ║${RESET}"
echo -e "${BOLD}${YELLOW}║   Production deploy:   git tag v*.* (CI/CD)          ║${RESET}"
echo -e "${BOLD}${YELLOW}╚══════════════════════════════════════════════════════╝${RESET}"
echo ""

if $DRY_RUN; then
  echo -e "${BOLD}${CYAN}   🔍  DRY RUN MODE — No changes will be made${RESET}"
  echo ""
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 0 — Load configuration from .env
# ─────────────────────────────────────────────────────────────────────────────
step "0 │ Loading configuration"

ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
  error ".env file not found."
  error "Copy .env.example → .env and fill in your values, then re-run."
  exit 1
fi

# shellcheck source=/dev/null
set -o allexport
source "$ENV_FILE"
set +o allexport

# Validate required variables
REQUIRED_VARS=(
  GOOGLE_CLOUD_PROJECT
  GOOGLE_CLOUD_REGION
  AR_REGISTRY
  AR_REPO
  IMAGE_NAME
)

# GKE variables are only required for deploy
if $RUN_DEPLOY; then
  REQUIRED_VARS+=(
    GKE_CLUSTER_NAME
    GKE_CLUSTER_ZONE
    HELM_RELEASE_NAME
    K8S_NAMESPACE
  )
fi

MISSING=()
for var in "${REQUIRED_VARS[@]}"; do
  if [ -z "${!var:-}" ]; then
    MISSING+=("$var")
  fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
  error "The following required variables are not set in .env:"
  for m in "${MISSING[@]}"; do
    error "  • $m"
  done
  exit 1
fi

# Derive the full image path
IMAGE_REPO="${AR_REGISTRY}/${GOOGLE_CLOUD_PROJECT}/${AR_REPO}/${IMAGE_NAME}"

# Derive image tag from current git commit SHA (short)
if git rev-parse --short HEAD > /dev/null 2>&1; then
  GIT_SHA=$(git rev-parse --short HEAD)
else
  warn "Not a git repo — falling back to timestamp-based tag."
  GIT_SHA="$(date +%Y%m%d%H%M%S)"
fi

IMAGE_TAG="${GIT_SHA}"
FULL_IMAGE="${IMAGE_REPO}:${IMAGE_TAG}"
LATEST_IMAGE="${IMAGE_REPO}:latest"

info "Project  : ${GOOGLE_CLOUD_PROJECT}"
info "Region   : ${GOOGLE_CLOUD_REGION}"
info "Image    : ${FULL_IMAGE}"
if $RUN_DEPLOY; then
  info "Cluster  : ${GKE_CLUSTER_NAME} (${GKE_CLUSTER_ZONE})"
  info "Namespace: ${K8S_NAMESPACE}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Prerequisite checks
# ─────────────────────────────────────────────────────────────────────────────
step "1 │ Checking prerequisites"

check_tool() {
  if ! command -v "$1" > /dev/null 2>&1; then
    error "Required tool not found: $1"
    error "  Install it and ensure it is on your PATH."
    exit 1
  fi
  success "$1 found ($(command -v "$1"))"
}

check_tool docker
check_tool gcloud

if $RUN_DEPLOY; then
  check_tool kubectl
  check_tool helm
fi

# Verify Docker daemon is running
if ! docker info > /dev/null 2>&1; then
  error "Docker daemon is not running. Start Docker Desktop and retry."
  exit 1
fi
success "Docker daemon is running"

# Verify gcloud auth
if ! gcloud auth print-access-token > /dev/null 2>&1; then
  error "Not authenticated to GCP. Run: gcloud auth login"
  exit 1
fi
success "GCP authentication verified"

# ─────────────────────────────────────────────────────────────────────────────
# DRY RUN — Show the plan and exit
# ─────────────────────────────────────────────────────────────────────────────
if $DRY_RUN; then
  step "Dry Run Summary"
  echo ""
  info "The following actions WOULD be performed:"
  echo ""
  echo -e "  ${BOLD}1.${RESET} docker build --file deploy/Dockerfile --tag ${FULL_IMAGE} ."
  if $RUN_PUSH; then
    echo -e "  ${BOLD}2.${RESET} gcloud auth configure-docker ${AR_REGISTRY}"
    echo -e "  ${BOLD}3.${RESET} docker push ${FULL_IMAGE}"
    echo -e "     docker push ${LATEST_IMAGE}"
  else
    echo -e "  ${DIM}2-3. (skipped — --no-push)${RESET}"
  fi
  if $RUN_DEPLOY; then
    echo -e "  ${BOLD}4.${RESET} gcloud container clusters get-credentials ${GKE_CLUSTER_NAME}"
    echo -e "  ${BOLD}5.${RESET} helm upgrade --install ${HELM_RELEASE_NAME} deploy/helm/"
    echo -e "     --namespace ${K8S_NAMESPACE}"
    echo -e "     --set image.tag=${IMAGE_TAG}"
    echo -e "  ${BOLD}6.${RESET} kubectl rollout status deployment/${HELM_RELEASE_NAME}"
  else
    echo -e "  ${DIM}4-6. (skipped — --no-deploy)${RESET}"
  fi
  echo ""
  info "No changes were made. Remove --dry-run to execute."
  exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# CONFIRMATION — Ask before proceeding
# ─────────────────────────────────────────────────────────────────────────────
step "Confirmation"

echo ""
warn "This will MANUALLY deploy outside the CI/CD pipeline."
echo ""
info "Image  : ${FULL_IMAGE}"
if $RUN_PUSH; then
  info "Push   : → Artifact Registry"
fi
if $RUN_DEPLOY; then
  info "Deploy : → GKE cluster '${GKE_CLUSTER_NAME}' / namespace '${K8S_NAMESPACE}'"
fi
echo ""

# Allow non-interactive usage with SKIP_CONFIRM=true
if [ "${SKIP_CONFIRM:-}" != "true" ]; then
  read -r -p "Continue? [y/N] " REPLY
  if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
    info "Aborted by user."
    exit 0
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Build Docker image
# ─────────────────────────────────────────────────────────────────────────────
step "2 │ Building Docker image"
info "Dockerfile : deploy/Dockerfile"
info "Build root : . (project root)"
info "Image      : ${FULL_IMAGE}"

docker build \
  --file deploy/Dockerfile \
  --tag "${FULL_IMAGE}" \
  --tag "${LATEST_IMAGE}" \
  --label "git-sha=${GIT_SHA}" \
  --label "build-date=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --label "manual-deploy=true" \
  .

success "Docker image built: ${FULL_IMAGE}"

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Push image to Artifact Registry
# ─────────────────────────────────────────────────────────────────────────────
step "3 │ Pushing image to Artifact Registry"

if ! $RUN_PUSH; then
  warn "Push skipped via --no-push flag."
else
  info "Configuring Docker authentication for ${AR_REGISTRY}..."
  gcloud auth configure-docker "${AR_REGISTRY}" --quiet

  info "Pushing ${FULL_IMAGE}..."
  docker push "${FULL_IMAGE}"

  info "Pushing ${LATEST_IMAGE}..."
  docker push "${LATEST_IMAGE}"

  success "Image pushed to Artifact Registry."
fi

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Deploy to GKE via Helm
# ─────────────────────────────────────────────────────────────────────────────
step "4 │ Deploying to GKE with Helm"

if ! $RUN_DEPLOY; then
  warn "Deploy skipped (--no-deploy or --no-push flag set)."
else
  # Get GKE credentials
  info "Fetching GKE credentials for cluster: ${GKE_CLUSTER_NAME}..."
  gcloud container clusters get-credentials "${GKE_CLUSTER_NAME}" \
    --zone "${GKE_CLUSTER_ZONE}" \
    --project "${GOOGLE_CLOUD_PROJECT}"

  success "kubectl context set to ${GKE_CLUSTER_NAME}."

  # Helm upgrade / install
  info "Running helm upgrade --install..."
  helm upgrade --install "${HELM_RELEASE_NAME}" deploy/helm/ \
    --namespace "${K8S_NAMESPACE}" \
    --create-namespace \
    --set image.repository="${IMAGE_REPO}" \
    --set image.tag="${IMAGE_TAG}" \
    --wait \
    --timeout 5m

  success "Helm release '${HELM_RELEASE_NAME}' deployed."

  # ─────────────────────────────────────────────────────────────────────────
  # STEP 5 — Verify rollout
  # ─────────────────────────────────────────────────────────────────────────
  step "5 │ Verifying rollout"

  DEPLOYMENT_NAME="${HELM_RELEASE_NAME}"
  info "Waiting for deployment '${DEPLOYMENT_NAME}' to roll out..."

  if kubectl rollout status deployment/"${DEPLOYMENT_NAME}" \
      --namespace "${K8S_NAMESPACE}" \
      --timeout=120s; then
    success "Deployment rolled out successfully."
  else
    error "Rollout did not complete within timeout."
    error "Check pod status: kubectl get pods -n ${K8S_NAMESPACE}"
    exit 1
  fi

  # Print pod status summary
  info "Pod status:"
  kubectl get pods --namespace "${K8S_NAMESPACE}" \
    --selector="app.kubernetes.io/instance=${HELM_RELEASE_NAME}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${GREEN}║   Manual Deploy Completed  ✓                ║${RESET}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════╝${RESET}"
info "Image  : ${FULL_IMAGE}"
if $RUN_DEPLOY; then
  info "Release: ${HELM_RELEASE_NAME} → namespace '${K8S_NAMESPACE}'"
fi
info "Log    : ${LOG_FILE}"
echo ""
warn "Remember: the primary deploy path is CI/CD (merge to main)."
echo ""
