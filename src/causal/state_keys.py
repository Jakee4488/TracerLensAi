"""Shared constants: control marker, session-state keys, and budgets.

Single source of truth for the agent-side pipeline. The proxy duplicates
CAUSAL_MODE_MARKER and the ``causal_`` key prefix (proxy/main.py) because the
proxy image does not ship the ``src`` package.

Keys are deliberately unprefixed by ADK scopes (``app:``/``user:``/``temp:``):
plain keys are session-scoped and persisted by VertexAiSessionService.
"""

CAUSAL_MODE_MARKER = "[[causal:on]]"
WEB_MODE_MARKER = "[[web:on]]"

# Session-state keys. Everything stored under these keys must be plain JSON.
KEY_GRAPH = "causal_graph"                       # to_ui_graph() shape for the UI
KEY_GRAPH_FULL = "causal_graph_full"             # full CausalGraph dump for rehydration
KEY_PLAN = "causal_plan"                         # ExecutionPlan dump
KEY_STEPS = "causal_steps"                       # list[str], human-readable trace lines
KEY_LEDGER = "causal_ledger"                     # list[ChangeRecord dumps]
KEY_STATUS = "causal_status"                     # CausalStatus dump
KEY_CURRENT_STEP = "causal_current_step"         # PlanStep dump or None
KEY_STEP_OUTPUT = "causal_step_output"           # raw executor output text
KEY_DECOMPOSITION_RAW = "causal_decomposition_raw"
KEY_REPLAN_REQUEST = "causal_replan_request"     # dict or None
KEY_REPLAN_RAW = "causal_replan_raw"
KEY_FINAL = "causal_final_answer"
KEY_BUDGETS = "causal_budgets"                   # {"max_steps": N, "max_replans": N}
KEY_QUERY = "causal_query"                       # original user problem text (marker stripped)
KEY_ESTIMAND_SPEC_RAW = "causal_estimand_spec_raw"  # CausalEstimand dump from the spec LLM
KEY_ESTIMAND = "causal_estimand"                 # IdentificationResult dump (UI-facing)
KEY_EFFECT = "causal_effect"                     # EffectEstimate dump or None (data path only)
KEY_COUNTERFACTUAL = "causal_counterfactual"     # CounterfactualResult dump or None (rung 3)
KEY_GRAPH_RECONCILE = "causal_graph_reconcile"   # GraphReconciliation dump or None (data path)
# Web-retrieval branch ([[web:on]]): best-effort observational data / evidence.
KEY_WEB_REQUESTED = "causal_web_requested"       # bool: web toggle on this turn
KEY_WEB_SEARCH_RAW = "causal_web_search_raw"     # raw CausalWebSearch LLM output text
KEY_WEB_DATASET = "causal_web_dataset"           # fenced CSV text fetched from the web, or None
KEY_WEB_EVIDENCE = "causal_web_evidence"         # list[str] evidence snippets
KEY_WEB_SOURCES = "causal_web_sources"           # list[str] source URLs
KEY_WEB_RETRIEVAL = "causal_web_retrieval"       # WebRetrieval dump (UI-facing)

# All keys the router resets at the start of a causal turn.
ALL_KEYS = (
    KEY_GRAPH, KEY_GRAPH_FULL, KEY_PLAN, KEY_STEPS, KEY_LEDGER, KEY_STATUS,
    KEY_CURRENT_STEP, KEY_STEP_OUTPUT, KEY_DECOMPOSITION_RAW,
    KEY_REPLAN_REQUEST, KEY_REPLAN_RAW, KEY_FINAL, KEY_BUDGETS, KEY_QUERY,
    KEY_ESTIMAND_SPEC_RAW, KEY_ESTIMAND, KEY_EFFECT, KEY_COUNTERFACTUAL,
    KEY_GRAPH_RECONCILE, KEY_WEB_REQUESTED, KEY_WEB_SEARCH_RAW, KEY_WEB_DATASET,
    KEY_WEB_EVIDENCE, KEY_WEB_SOURCES, KEY_WEB_RETRIEVAL,
)

# Budgets and caps (env-overridable where noted in agents.py/router.py).
DEFAULT_MAX_STEPS = 8
DEFAULT_MAX_REPLANS = 2
MAX_COMPONENTS = 12
MAX_EDGES = 24
LOOP_MAX_ITERATIONS = 16
LEDGER_CAP = 50

# Executor output trailer contract (see prompts.step_executor_instruction).
STEP_STATUS_RE = r"STEP_STATUS:\s*(success|failure)"
OBSERVED_RE = r"OBSERVED:\s*(.+)"

# Fenced-block language for the optional text-transport fallback.
FENCED_BLOCK_LANG = "causal-json"
