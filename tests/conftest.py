import json

import pytest
from fastapi.testclient import TestClient

from proxy.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _fast_mock_stream(monkeypatch):
    """Drop the offline mock stream's pacing so unit tests don't sleep.

    The 150ms/frame default exists so a human (and the Playwright suite) can
    watch stages land; it is pure cost here.
    """
    monkeypatch.setenv("MOCK_FRAME_DELAY_S", "0")


# ── SSE helpers ──────────────────────────────────────────────────────────────
# /analyze-prompt streams Server-Sent Events. Tests assert on the frames rather
# than a JSON body.

def sse_frames(response):
    """Parse an SSE response body into a list of (event_name, payload)."""
    frames = []
    for block in response.text.split("\n\n"):
        if not block.strip():
            continue
        name = "message"
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:  # skip ": ping" keepalive comments
            frames.append((name, json.loads("\n".join(data_lines))))
    return frames


def sse_report(response):
    """Return the terminal `done` payload — the report body of a run."""
    for name, payload in sse_frames(response):
        if name == "done":
            return payload
    raise AssertionError(
        f"no `done` frame in stream; frames were "
        f"{[n for n, _ in sse_frames(response)]}")
