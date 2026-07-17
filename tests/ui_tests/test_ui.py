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
    expect(steps).to_have_count(3)  # canned mock steps
    expect(page.locator(".causal-graph-container svg")).to_have_count(1, timeout=15000)
    expect(page.locator(".phase-badge")).to_contain_text("complete")


def test_previous_graph_survives_new_message(page: Page, server):
    # Regression test for the old innerHTML+= re-parse bug that wiped
    # previously rendered Mermaid SVGs on every append.
    page.goto(server)
    page.check("#causal-toggle")
    send_prompt(page, "First causal question")
    expect(page.locator(".causal-graph-container svg")).to_have_count(1, timeout=15000)
    send_prompt(page, "Second causal question")
    expect(page.locator(".causal-graph-container svg")).to_have_count(2, timeout=15000)


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
