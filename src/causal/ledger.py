"""Change ledger: an append-only, capped record of what each step changed.

State deltas replace whole values, so appends always return a NEW list —
never mutate the list already stored in session state.
"""

from __future__ import annotations

from typing import Optional

from src.causal.models import ChangeRecord
from src.causal.state_keys import LEDGER_CAP


def append_record(ledger: Optional[list[dict]], record: ChangeRecord,
                  cap: int = LEDGER_CAP) -> list[dict]:
    """Return a new ledger list with the record appended, oldest entries
    dropped past the cap."""
    entries = list(ledger or [])
    entries.append(record.model_dump(mode="json"))
    return entries[-cap:]


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
