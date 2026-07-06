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

    # Verify that a new AI message appears in the messages area
    ai_messages = page.locator("#messages-area .message.ai")
    
    # Wait until there are at least 2 AI messages (1 welcome + 1 response)
    expect(ai_messages).to_have_count(2, timeout=10000)

