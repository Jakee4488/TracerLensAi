"""Change ledger: an append-only, capped record of what each step changed.

State deltas replace whole values, so appends always return a NEW list —
never mutate the list already stored in session state.
"""

from __future__ import annotations

from typing import MutableMapping, Optional

from src.causal.models import ChangeRecord, ReplanEvent
from src.causal.state_keys import (
    KEY_LEDGER,
    KEY_LEDGER_DROPPED,
    KEY_REPLAN_EVENTS,
    LEDGER_CAP,
)


def append_record(ledger: Optional[list[dict]], record: ChangeRecord,
                  cap: int = LEDGER_CAP) -> list[dict]:
    """Return a new ledger list with the record appended, oldest entries
    dropped past the cap."""
    entries = list(ledger or [])
    entries.append(record.model_dump(mode="json"))
    return entries[-cap:]


def append_to_state(state: MutableMapping, record: ChangeRecord,
                    cap: int = LEDGER_CAP,
                    sink: Optional[MutableMapping] = None) -> None:
    """Append to the ledger, counting entries lost to the cap.

    A capped ledger that silently drops its oldest entries is an audit trail
    that lies by omission — a long run loses its head with nothing saying so.
    The running count is kept beside the ledger and surfaced in the UI.

    Current values are always read from ``state``; writes go to ``sink`` when
    given, so callers that batch into an ADK ``state_delta`` dict (the step
    controller) and callers that write straight to session state (the
    callbacks) share one implementation.
    """
    target = state if sink is None else sink
    previous = state.get(KEY_LEDGER)
    if len(previous or []) >= cap:
        target[KEY_LEDGER_DROPPED] = int(state.get(KEY_LEDGER_DROPPED) or 0) + 1
    target[KEY_LEDGER] = append_record(previous, record, cap)


def append_replan_event(state: MutableMapping, event: ReplanEvent,
                        cap: int = LEDGER_CAP,
                        sink: Optional[MutableMapping] = None) -> None:
    """Persist the structured record of *why* the plan changed.

    This used to be built by ``graph_engine.splice``, flattened to one prose
    line by ``runtime.summarize_replan_line`` and then dropped — so which steps
    were invalidated, which replaced them, and the version transition were not
    recoverable after the turn.
    """
    target = state if sink is None else sink
    entries = list(state.get(KEY_REPLAN_EVENTS) or [])
    entries.append(event.model_dump(mode="json"))
    target[KEY_REPLAN_EVENTS] = entries[-cap:]


def next_seq(ledger: Optional[list[dict]]) -> int:
    entries = ledger or []
    if not entries:
        return 1
    return max(int(e.get("seq", 0)) for e in entries) + 1


def latest_for_step(ledger: Optional[list[dict]], step_id: str) -> Optional[dict]:
    for entry in reversed(ledger or []):
        if entry.get("step_id") == step_id:
            return entry
    return None
