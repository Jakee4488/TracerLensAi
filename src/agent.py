"""TracerLensAi - ADK Agent Definition.

This module defines the agent logic using the Agent Development Kit (ADK).
It configures the agent to use the Memory Bank for persistent sessions and
enables the Google Search and Code Execution tools.
"""

import logging
import os
from typing import Iterator

from google.adk.agents import Agent
from google.adk.tools import google_search
from google.adk.code_executors import BuiltInCodeExecutor
from google.adk.apps import App
from vertexai.agent_engines.templates.adk import AdkApp

from src.app_utils import services
from src.causal.agents import build_root_agent

# agents-cli injects GOOGLE_CLOUD_LOCATION=global, but this project's Gemini
# quota on the global endpoint has been exhausted while europe-west2 (where
# the rest of the stack lives) is healthy. Rewrite it before any Gemini
# client is constructed, since the ADK/genai client snapshots this env var
# at build time. Guarded so a deliberately-set region is left alone.
if os.environ.get("GOOGLE_CLOUD_LOCATION") == "global":
    os.environ["GOOGLE_CLOUD_LOCATION"] = os.environ.get(
        "GOOGLE_CLOUD_REGION", "europe-west2"
    )

# General assistant: the pre-existing single agent, now one branch of the
# deterministic root router (the other branch is the causal pipeline).
general_assistant = Agent(
    name="GeneralAssistant",
    description="An advanced causal reasoning agent capable of mathematical modeling and deep analysis.",
    model="gemini-2.5-flash",
    instruction=(
        "You are TracerLensAi, an expert AI assistant specializing in causal reasoning, "
        "data analysis, and structural causal models. You can execute Python code to perform "
        "complex mathematical calculations and use Google Search to look up current information. "
        "When asked to perform causal reasoning, identify potential confounders, propose a structural "
        "causal model, and estimate treatment effects step-by-step. "
        "Ignore control markers such as [[causal:on]] if they appear in a message."
    ),
    # Enable Google Search (Commented out because Vertex AI does not allow mixing Search with Code Execution)
    # tools=[google_search],

    # Code execution uses the specific code_executor arg in ADK 2.x
    code_executor=BuiltInCodeExecutor(),
)

# Root: deterministic marker router in front of the causal pipeline and the
# general assistant (see src/causal/ for the pathway engine).
agent = build_root_agent(general_assistant)

adk_app = App(
    root_agent=agent,
    name="TracerLensAi_App",
)

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
            logging.warning(f"Failed to create default session in set_up: {e}")

    def query(
        self,
        message: str,
        user_id: str = "default",
        session_id: str = "default",
    ) -> str:
        """Standard synchronous query method expected by Vertex AI default endpoints."""
        # Ensure session exists to prevent SessionNotFoundError
        try:
            self.wrapper.create_session(user_id=user_id, session_id=session_id)
        except Exception as e:
            logging.warning(f"Failed to create session {session_id} for user {user_id} during query: {e}")

        # AdkApp registers stream_query only (no sync query); drain the stream
        # and concatenate the text parts for a synchronous response.
        collected: list[str] = []
        for event in self.wrapper.stream_query(
            message=message, user_id=user_id, session_id=session_id
        ):
            if isinstance(event, dict):
                for part in (event.get("content") or {}).get("parts") or []:
                    if isinstance(part, dict) and part.get("text"):
                        collected.append(part["text"])
            else:
                collected.append(str(event))
        return "".join(collected)

    def stream_query(
        self,
        message: str,
        user_id: str = "default",
        session_id: str = "default",
    ) -> Iterator[str]:
        # Ensure session exists to prevent SessionNotFoundError
        try:
            self.wrapper.create_session(user_id=user_id, session_id=session_id)
        except Exception as e:
            logging.warning(f"Failed to create session {session_id} for user {user_id} during stream_query: {e}")

        # Delegate to the ADK App wrapper
        return self.wrapper.stream_query(message=message, user_id=user_id, session_id=session_id)

app = TracerLensEngine(adk_wrapper)

# Expose root_agent for the ADK dev server
root_agent = agent

if __name__ == "__main__":
    print("Agent initialized successfully.")