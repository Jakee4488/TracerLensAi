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


# ── Effect chart & drill-down drawer ──────────────────────────────────────────


def test_effect_chart_replaces_text_estimate(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Does price affect demand?")
    expect(page.locator(".effect-chart")).to_be_visible(timeout=20000)
    # The canned CI (-1.61, -1.23) sits entirely below zero.
    expect(page.locator(".effect-verdict")).to_have_text("excludes zero")
    expect(page.locator(".effect-whisker")).to_have_count(1)
    expect(page.locator(".effect-point")).to_have_count(1)
    # Values stay readable without hovering anything.
    expect(page.locator(".effect-point-value")).to_have_text("-1.42")
    expect(page.locator(".axis-tick.zero")).to_have_text("0")


def test_refutations_share_the_effect_scale(page: Page, server):
    """The caption claims one scale; the plot column must literally be the same
    pixels, or the dots are not comparable to the interval above them."""
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Does price affect demand?")
    expect(page.locator(".refute-track")).to_have_count(2, timeout=20000)
    box = "el => { const r = el.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.width)]; }"
    effect_track = page.locator(".effect-track").evaluate(box)
    for i in range(2):
        assert page.locator(".refute-track").nth(i).evaluate(box) == effect_track, \
            "refutation track is not aligned with the effect track"
    # Pass/fail never rides on colour alone.
    expect(page.locator(".refute-verdict").first).to_contain_text("pass")


def test_node_click_opens_drawer(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    # Drill-down lives on the finished panel — mid-run the ledger is partial.
    expect(page.locator(".timeline-summary")).to_be_visible(timeout=25000)
    page.locator(".dag-node", has_text="Analysis").click()
    drawer = page.locator(".step-drawer")
    expect(drawer).to_be_visible()
    expect(page.locator(".drawer-title")).to_have_text("Analysis")
    # expected/observed come straight off ChangeRecord.
    expect(drawer).to_contain_text("estimator returned no rows")
    page.keyboard.press("Escape")
    expect(drawer).to_have_count(0)


def test_drawer_affected_chips_highlight_the_dag(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    # Drill-down lives on the finished panel — mid-run the ledger is partial.
    expect(page.locator(".timeline-summary")).to_be_visible(timeout=25000)
    page.locator(".dag-node", has_text="Analysis").click()
    chip = page.locator(".affected-chip")
    expect(chip).to_have_count(1)          # the failed step invalidated `outcome`
    expect(page.locator(".dag-node.highlighted")).to_have_count(0)
    chip.hover()
    expect(page.locator(".dag-node.highlighted")).to_have_count(1)


def test_drawer_empty_state_for_unexecuted_component(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    # Drill-down lives on the finished panel — mid-run the ledger is partial.
    expect(page.locator(".timeline-summary")).to_be_visible(timeout=25000)
    page.locator(".dag-node", has_text="Outcome").click()
    expect(page.locator(".drawer-empty")).to_contain_text("never executed")


# ── Traceability & explainability ─────────────────────────────────────────────
# Each of these renders something the pipeline already produced but which never
# reached a reader: the correlation id, the execution plan, the replan events,
# and the decomposer's per-edge rationale.


def test_run_id_is_shown_for_the_turn(page: Page, server):
    """The id that joins this answer to its server log line and Cloud Trace."""
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    strip = page.locator(".run-id-strip code")
    expect(strip).to_be_visible(timeout=25000)
    assert (strip.text_content() or "").strip(), "run id rendered empty"


def test_plan_is_rendered_with_status_and_detail(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    expect(page.locator(".plan-card")).to_be_visible(timeout=25000)
    # The mock plan is s1 done / s2 failed / s3 done (replanned).
    expect(page.locator(".plan-step")).to_have_count(3)
    expect(page.locator(".plan-step.failed")).to_have_count(1)

    # Objective, expected effect and result are the "what did it intend" record.
    page.locator(".plan-step.failed .plan-step-toggle").click()
    detail = page.locator(".plan-step.failed .plan-detail")
    expect(detail).to_contain_text("expected")
    expect(detail).to_contain_text("result")


def test_replan_narrative_explains_what_changed(page: Page, server):
    """ReplanEvent used to be flattened to one trace line and dropped."""
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    event = page.locator(".replan-event")
    expect(event).to_have_count(1, timeout=25000)
    expect(event).to_contain_text("s2")           # the step that failed
    expect(event).to_contain_text("v1")           # plan version transition
    expect(event).to_contain_text("v2")
    expect(page.locator(".replan-detail")).to_contain_text("s3")  # what replaced it


def test_node_drawer_shows_why_the_component_exists(page: Page, server):
    """description/rationale were captured by the models and dropped in
    to_ui_graph() before they could reach anyone."""
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    expect(page.locator(".estimand-card")).to_be_visible(timeout=25000)
    page.locator(".dag-node", has_text="Analysis").click()

    drawer = page.locator(".step-drawer")
    expect(drawer).to_contain_text("why this component")
    expect(drawer).to_contain_text("Adjusts for the confounder")
    # ...and the incoming edge's justification.
    expect(drawer).to_contain_text("why it is caused")
    expect(drawer).to_contain_text("cannot run without the observed rows")


def test_export_is_disabled_until_the_run_lands(page: Page, server):
    """Mid-flight the panel runs off the live graph, so an export would be a
    near-empty file."""
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    expect(page.locator(".export-run-btn")).to_be_disabled()
    expect(page.locator(".export-run-btn")).to_be_enabled(timeout=25000)


def test_run_exports_as_one_auditable_file(page: Page, server):
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "Why does it rain?")
    # Wait for the terminal report, not just the header control.
    expect(page.locator(".estimand-card")).to_be_visible(timeout=25000)

    with page.expect_download() as download:
        page.click(".export-run-btn")
    import json
    with open(download.value.path(), encoding="utf-8") as handle:
        payload = json.load(handle)

    assert payload["run_id"]
    assert len(payload["plan"]["steps"]) == 3
    assert len(payload["replan_events"]) == 1
    assert payload["graph"]["edges"][0]["rationale"]
    assert payload["identification"]["estimand_type"] == "backdoor"


# ── Responsive sidebar (narrow viewports) ─────────────────────────────────────
# Regression coverage for a bug found via manual testing: below the 900px
# breakpoint the sidebar overlays the page (styles.css .sidebar position:
# absolute), and if it's shown by default there is no way to reach the
# hamburger button underneath it to dismiss it — the app becomes unusable.


def test_sidebar_starts_open_on_desktop_width(page: Page, server):
    page.set_viewport_size({"width": 1300, "height": 900})
    page.goto(server)
    expect(page.locator("#sidebar")).not_to_have_class("sidebar collapsed")


def test_sidebar_starts_collapsed_below_breakpoint(page: Page, server):
    page.set_viewport_size({"width": 420, "height": 800})
    page.goto(server)
    expect(page.locator("#sidebar")).to_have_class("sidebar collapsed")
    # ...and nothing spills outside the viewport at this width.
    overflow = page.evaluate("() => document.body.scrollWidth > document.body.clientWidth")
    assert not overflow, "horizontal overflow at 420px viewport width"


def test_sidebar_toggle_survives_repeated_open_close_at_narrow_width(page: Page, server):
    """The hamburger sits underneath the sidebar's overlay; it must stay
    clickable through repeated open/close, not just the first tap."""
    page.set_viewport_size({"width": 420, "height": 800})
    page.goto(server)
    sidebar = page.locator("#sidebar")
    for _ in range(3):
        page.click("#toggle-sidebar", timeout=3000)
        expect(sidebar).not_to_have_class("sidebar collapsed")
        page.click("#toggle-sidebar", timeout=3000)
        expect(sidebar).to_have_class("sidebar collapsed")
