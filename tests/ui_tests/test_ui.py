"""Playwright E2E tests for the TracerLensAi UI against the mock-mode proxy.

The mock /analyze-prompt echoes the prompt, reports 10 tokens, returns a
canned causal graph when causal reasoning is on, and acknowledges attachments
with "Attached files (N): ..." — every assertion here keys on that contract
(see proxy/main.py mock branch).
"""
from playwright.sync_api import Page, expect


def send_prompt(page: Page, text: str):
    page.fill("#chat-input", text)
    page.click("#send-btn")


def test_page_loads(page: Page, server, console_errors):
    page.goto(server)
    assert "TracerLensAi" in page.title()
    expect(page.locator("#messages-area")).to_be_visible()
    expect(page.locator(".msg.ai .bubble").first).to_contain_text("Causal Agent")
    expect(page.locator("#sidebar")).to_be_visible()
    expect(page.locator("#send-btn")).to_be_visible()
    page.wait_for_timeout(1500)  # let deferred scripts settle before checking console
    assert console_errors() == []


def test_theme_toggle_persists(page: Page, server):
    page.goto(server)
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.click("#theme-toggle")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    # restore for other tests sharing the storage state
    page.click("#theme-toggle")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


def test_send_message_mock_roundtrip(page: Page, server, console_errors):
    page.goto(server)
    send_prompt(page, "Hello agent")
    expect(page.locator(".msg.user .bubble")).to_contain_text("Hello agent")
    expect(page.locator(".msg.ai .bubble").last).to_contain_text("Agent Proxy configured")
    expect(page.locator("#token-tally-badge")).to_have_text("10 tokens used")
    expect(page.locator(".typing")).to_have_count(0)
    assert console_errors() == []


def test_causal_toggle_renders_graph_and_steps(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    expect(page.locator(".causal-panel")).to_be_visible()
    steps = page.locator(".causal-steps li")
    expect(steps).to_have_count(4)  # 3 canned mock steps + the graph-fix line
    expect(page.locator(".causal-graph-container")).to_have_count(1, timeout=15000)
    # inputs -> analysis -> outcome
    expect(page.locator(".causal-graph-container .react-flow__node")).to_have_count(3)
    expect(page.locator(".phase-badge")).to_contain_text("complete")


def test_previous_graph_survives_new_message(page: Page, server):
    # Regression test for the old innerHTML+= re-parse bug that wiped
    # previously rendered diagrams on every append.
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "First causal question")
    expect(page.locator(".causal-graph-container")).to_have_count(1, timeout=15000)
    send_prompt(page, "Second causal question")
    expect(page.locator(".causal-graph-container")).to_have_count(2, timeout=15000)
    expect(page.locator(".causal-graph-container .react-flow__node")).to_have_count(6)


def test_upload_flow(page: Page, server, sample_txt):
    page.goto(server)
    page.set_input_files("#file-input", str(sample_txt))
    chip = page.locator("#attachment-chips .attach-chip")
    expect(chip).to_have_count(1)
    expect(chip).to_contain_text("sample.txt")
    # wait for the upload POST to finish (chip leaves the uploading state)
    expect(page.locator("#attachment-chips .attach-chip.uploading")).to_have_count(0)
    expect(page.locator("#attachment-chips .attach-chip.error")).to_have_count(0)

    send_prompt(page, "Summarise the attachment")
    expect(page.locator(".msg.ai .bubble").last).to_contain_text("Attached files (1): sample.txt")
    # user bubble shows the attachment name; composer chips cleared after send
    expect(page.locator(".msg.user .bubble-attachment")).to_contain_text("sample.txt")
    expect(page.locator("#attachment-chips .attach-chip")).to_have_count(0)


def test_attachment_chip_remove(page: Page, server, sample_txt):
    page.goto(server)
    page.set_input_files("#file-input", str(sample_txt))
    expect(page.locator("#attachment-chips .attach-chip.uploading")).to_have_count(0)
    page.click("#attachment-chips .chip-x")
    expect(page.locator("#attachment-chips .attach-chip")).to_have_count(0)

    send_prompt(page, "No attachment now")
    expect(page.locator(".msg.ai .bubble").last).to_contain_text("Agent Proxy configured")
    expect(page.locator(".msg.ai .bubble").last).not_to_contain_text("Attached files")


def test_upload_rejects_bad_type_ui(page: Page, server, sample_exe):
    page.goto(server)
    page.set_input_files("#file-input", str(sample_exe))
    chip = page.locator("#attachment-chips .attach-chip")
    expect(chip).to_have_count(1)
    expect(page.locator("#attachment-chips .attach-chip.error")).to_have_count(1)
    # an errored chip is never sent
    send_prompt(page, "Try sending anyway")
    expect(page.locator(".msg.ai .bubble").last).not_to_contain_text("Attached files")


def test_new_chat_clears(page: Page, server):
    page.goto(server)
    send_prompt(page, "Some message")
    expect(page.locator("#token-tally-badge")).to_have_text("10 tokens used")
    page.click("#new-chat-btn")
    expect(page.locator(".msg")).to_have_count(1)  # greeting only
    expect(page.locator("#token-tally-badge")).to_have_text("0 tokens used")


def test_sidebar_collapse(page: Page, server):
    page.goto(server)
    sidebar = page.locator("#sidebar")
    expect(sidebar).not_to_have_class("sidebar collapsed")
    page.click("#toggle-sidebar")
    expect(sidebar).to_have_class("sidebar collapsed")
    page.click("#toggle-sidebar")
    expect(sidebar).to_have_class("sidebar")


def test_markdown_is_sanitized(page: Page, server):
    # The mock echoes the prompt back into the AI response, which flows
    # through marked.parse — a perfect XSS probe. DOMPurify must strip the
    # onerror handler before insertion.
    page.goto(server)
    send_prompt(page, '<img src=x onerror="window.__xss=1">')
    expect(page.locator(".msg.ai .bubble").last).to_contain_text("Agent Proxy configured")
    page.wait_for_timeout(500)
    assert page.evaluate("window.__xss") is None


# ── Live workflow timeline ────────────────────────────────────────────────────
# The mock proxy emits a scripted SSE stream (progress + graph frames, ~150ms
# apart), so these can observe the run mid-flight rather than only its result.


def test_timeline_replaces_dots_in_causal_mode(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    # The nine-stage pipeline is shown instead of the three-dot indicator.
    expect(page.locator(".workflow-timeline")).to_be_visible()
    expect(page.locator(".stage-row")).to_have_count(9)
    expect(page.locator(".typing")).to_have_count(0)


def test_timeline_stages_advance_during_run(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    # Stages resolve in order while the run is still in flight: exactly one is
    # active, and earlier ones have already completed.
    expect(page.locator(".stage-row.active")).to_have_count(1)
    expect(page.locator(".stage-row.done").first).to_be_visible()


def test_timeline_collapses_to_summary_when_done(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    summary = page.locator(".timeline-summary")
    expect(summary).to_be_visible(timeout=20000)
    expect(summary).to_contain_text("stages")
    # Collapsed by default so replayed history isn't dominated by it...
    expect(page.locator(".stage-row")).to_have_count(0)
    summary.click()
    # ...and expandable, showing only the stages that actually ran.
    expect(page.locator(".stage-row")).not_to_have_count(0)
    expect(page.locator(".stage-row.skipped")).to_have_count(0)


def test_graph_renders_before_final_answer(page: Page, server):
    """The DAG arrives mid-run on a `graph` frame, not with the report."""
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    expect(page.locator(".causal-graph-container .react-flow__node")).to_have_count(3, timeout=15000)
    # Still streaming: the finished panel's summary strip has not appeared yet.
    expect(page.locator(".timeline-summary")).to_have_count(0)


def test_non_causal_mode_keeps_typing_dots(page: Page, server):
    page.goto(server)
    send_prompt(page, "Hello agent")
    # No pipeline runs without the toggle, so there is nothing to time-line.
    expect(page.locator(".workflow-timeline")).to_have_count(0)


def test_timeline_readable_with_reduced_motion(page: Page, server):
    """Several animations encode their end state in a transform; with motion
    disabled they must not sit at their start value and vanish."""
    page.emulate_media(reduced_motion="reduce")
    try:
        page.goto(server)
        page.check("#causal-toggle")
        send_prompt(page, "Why does it rain?")
        expect(page.locator(".stage-row")).to_have_count(9)
        step = page.locator(".stage-steps li").first
        expect(step).to_be_visible(timeout=15000)
        opacity = step.evaluate("el => getComputedStyle(el).opacity")
        assert float(opacity) == 1.0, f"trace lines invisible with reduced motion ({opacity})"
    finally:
        page.emulate_media(reduced_motion="no-preference")


# ── Live DAG animation ────────────────────────────────────────────────────────


def test_dag_nodes_animate_through_statuses(page: Page, server):
    """The mock walks each node pending -> active -> done on its own `graph`
    frame, so the class must actually change while the run is in flight."""
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    expect(page.locator(".dag-node")).to_have_count(3, timeout=15000)
    # Some node reaches `active` mid-run...
    expect(page.locator(".dag-node.active")).not_to_have_count(0)
    # ...and all of them are done once the run finishes.
    expect(page.locator(".timeline-summary")).to_be_visible(timeout=20000)
    expect(page.locator(".dag-node.done")).to_have_count(3)


def test_dag_does_not_relayout_on_status_change(page: Page, server):
    """Positions are computed once, on the first graph frame. A status-only
    frame must not move anything — otherwise the diagram jumps on every
    executor iteration."""
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    node = page.locator(".react-flow__node").first
    expect(node).to_be_visible(timeout=15000)

    read = "el => el.style.transform"
    before = node.evaluate(read)
    # Wait for at least one more graph frame to land (statuses still changing).
    expect(page.locator(".dag-node.done")).not_to_have_count(0, timeout=20000)
    after = node.evaluate(read)
    assert before == after, f"node re-laid-out mid-run: {before!r} -> {after!r}"


def test_critical_path_is_marked(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    # The canned critical path covers all three nodes.
    expect(page.locator(".dag-node.critical")).to_have_count(3, timeout=15000)
