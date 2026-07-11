"""TracerLensAi - ADK Agent Definition.

This module defines the agent logic using the Agent Development Kit (ADK).
It configures the agent to use the Memory Bank for persistent sessions and
enables the Google Search and Code Execution tools.
"""

from google.adk.agents import Agent
from google.adk.tools import google_search
from google.adk.code_executors import BuiltInCodeExecutor

# Initialize the Gemini Enterprise Agent
agent = Agent(
    name="TracerLensAi_Agent",
    description="An advanced causal reasoning agent capable of mathematical modeling and deep analysis.",
    model="gemini-2.5-flash",
    instruction=(
        "You are TracerLensAi, an expert AI assistant specializing in causal reasoning, "
        "data analysis, and structural causal models. You can execute Python code to perform "
        "complex mathematical calculations and use Google Search to look up current information. "
        "When asked to perform causal reasoning, identify potential confounders, propose a structural "
        "causal model, and estimate treatment effects step-by-step."
    ),
    # Enable Google Search (Commented out because Vertex AI does not allow mixing Search with Code Execution)
    # tools=[google_search],

    # Code execution uses the specific code_executor arg in ADK 2.x
    code_executor=BuiltInCodeExecutor(),
)

from google.adk.apps import App

adk_app = App(
    root_agent=agent,
    name="TracerLensAi_App",
)

from vertexai.agent_engines.templates.adk import AdkApp
from src.app_utils import services
from typing import Iterator

adk_wrapper = AdkApp(
    app=adk_app,
    session_service_builder=services.get_session_service,
    artifact_service_builder=services.get_artifact_service,
)

class TracerLensEngine:
    """Custom wrapper to force schema registration for the ADK App."""
    def __init__(self, wrapper):
        self.wrapper = wrapper

    def set_up(self):
        if hasattr(self.wrapper, "set_up"):
            self.wrapper.set_up()
        try:
            # Pre-create the default session so stream_query doesn't fail
            self.wrapper.create_session(user_id="default-user", session_id="default-session")
        except Exception as e:
            print(f"Failed to create default session in set_up: {e}")

    def stream_query(
        self,
        message: str,
        user_id: str = "default",
        session_id: str = "default",
    ) -> Iterator[str]:
        # Ensure session exists to prevent SessionNotFoundError
        try:
            self.wrapper.create_session(user_id=user_id, session_id=session_id)
        except Exception:
            pass

        # Delegate to the ADK App wrapper
        return self.wrapper.stream_query(message=message, user_id=user_id, session_id=session_id)

app = TracerLensEngine(adk_wrapper)

# Expose root_agent for the ADK dev server
root_agent = agent

if __name__ == "__main__":
    print("Agent initialized successfully.")
