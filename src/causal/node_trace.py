"""Node-level instrumentation: what each reasoning node computed.

The change ledger (``ledger.py``) records *what a step changed*. This records
*what a node computed* — its inputs, its outputs, and the numeric quantities it
produced — so the full reasoning path is recoverable, not just the final answer.

Two consumers, one write:

- **Session state** (``causal_node_traces``), like every other pipeline write,
  so the proxy/UI can read it and it persists with the turn.
- **The evaluation layer**, via the fenced ``causal-nodes`` block that
  ``CausalNodeTraceEmitter`` emits when ``CAUSAL_NODE_TRACE=1``. This second
  channel is not redundancy: ADK eval traces preserve only each event's
  ``author`` and ``content``, so a ``state_delta`` is invisible to a grader and
  a deterministic ``BaseAgent`` that yields no content does not appear in the
  trace at all. Content is the only channel that reaches the eval layer.

Everything here is pure (no ADK, no I/O, no LLM) so it unit-tests hermetically.
State deltas replace whole values, so appends always return a NEW list — never
mutate the list already in session state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, MutableMapping, Optional

from src.causal.models import NodeTrace
from src.causal.state_keys import (
    KEY_NODE_TRACES,
    KEY_NODE_TRACES_DROPPED,
    NODE_TRACE_CAP,
)


def next_seq(traces: Optional[list[dict]]) -> int:
    entries = traces or []
    if not entries:
        return 1
    return max(int(e.get("seq", 0)) for e in entries) + 1


def append_trace(traces: Optional[list[dict]], trace: NodeTrace,
                 cap: int = NODE_TRACE_CAP) -> list[dict]:
    """Return a new list with the trace appended, oldest dropped past the cap."""
    entries = list(traces or [])
    entries.append(trace.model_dump(mode="json"))
    return entries[-cap:]


def record(state: MutableMapping, *, node_id: str, node_kind: str,
           stage: str, inputs: Optional[dict[str, Any]] = None,
           outputs: Optional[dict[str, Any]] = None,
           values: Optional[dict[str, Any]] = None,
           note: str = "", cap: int = NODE_TRACE_CAP,
           sink: Optional[MutableMapping] = None) -> NodeTrace:
    """Build and persist one node trace; returns it for the caller's own use.

    Current values are always read from ``state``; writes go to ``sink`` when
    given, so callers batching into an ADK ``state_delta`` (the controller, the
    estimator) and callers writing straight to session state (the callbacks)
    share one implementation — same split as ``ledger.append_to_state``.
    """
    target = state if sink is None else sink
    # Chained records inside ONE state_delta must see each other. Reading only
    # from `state` would compute every trace in the batch from the same
    # pre-batch list, so each write clobbers the last and a stage that records
    # several nodes (the estimator: identification, effect, counterfactual)
    # would persist just its final one. Prefer the pending value in the sink.
    if sink is not None and KEY_NODE_TRACES in sink:
        previous = sink.get(KEY_NODE_TRACES)
        dropped_from = sink
    else:
        previous = state.get(KEY_NODE_TRACES)
        dropped_from = state
    trace = NodeTrace(
        seq=next_seq(previous),
        node_id=node_id,
        node_kind=node_kind,  # type: ignore[arg-type]
        stage=stage,
        inputs=inputs or {},
        outputs=outputs or {},
        values=values or {},
        note=note,
        ts=datetime.now(timezone.utc).isoformat(),
    )
    if len(previous or []) >= cap:
        target[KEY_NODE_TRACES_DROPPED] = int(dropped_from.get(KEY_NODE_TRACES_DROPPED) or 0) + 1
    target[KEY_NODE_TRACES] = append_trace(previous, trace, cap)
    return trace


# ── Read helpers (used by the eval layer and by tests) ───────────────────────

def visited_node_ids(traces: Optional[list[dict]]) -> list[str]:
    """Node ids in the order the reasoning actually visited them.

    Duplicates are preserved: a component executed twice (a replan retry) is
    genuinely two visits, and collapsing them would hide the retry.
    """
    return [str(t.get("node_id", "")) for t in (traces or []) if t.get("node_id")]


def values_index(traces: Optional[list[dict]]) -> dict[str, float]:
    """Flatten every node's numeric values into ``"<node_id>.<name>" -> float``.

    Later entries win, so a component re-executed after a replan reports the
    value it finally settled on rather than its first attempt.
    """
    flat: dict[str, float] = {}
    for entry in traces or []:
        node_id = str(entry.get("node_id", ""))
        for name, value in (entry.get("values") or {}).items():
            try:
                flat[f"{node_id}.{name}"] = float(value)
            except (TypeError, ValueError):
                continue
    return flat


def traces_of_kind(traces: Optional[list[dict]], kind: str) -> list[dict]:
    return [t for t in (traces or []) if t.get("node_kind") == kind]
