import pytest
from playwright.sync_api import Page, expect

def test_causal_agent_chat_interaction(page: Page):
    """
    Automated UI test for the Causal Agent chat interface.
    This runs inside the ui-test-runner Docker container.
    """
    # Navigate to the locally running app in the Docker Compose network
    # The app is accessible via the service name 'tracerlensai-app'
    page.goto("http://tracerlensai-app:8080/")

    # Wait for the UI to load and verify the title/header
    expect(page).to_have_title("TracerLensAi: AI Agentic Workflow Evaluator")
    expect(page.locator(".header-title")).to_contain_text("Causal Agent")

    # Interact with the input
    chat_input = page.locator("#chat-input")
    chat_input.fill("Analyze the causal impact of recent deployments.")
    
    # Click send
    page.click("#send-btn")

    # Verify that a new evaluation card appears in the messages area
    eval_cards = page.locator("#messages-area .evaluation-card")
    
    # Wait until there is at least 1 evaluation card
    expect(eval_cards).to_have_count(1, timeout=10000)

