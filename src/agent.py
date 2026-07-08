"""TracerLensAi - ADK Agent Definition.

This module defines the agent logic using the Agent Development Kit (ADK).
It configures the agent to use the Memory Bank for persistent sessions and
enables the Google Search and Code Execution tools.
"""

from adk import Agent

# Initialize the Gemini Enterprise Agent
agent = Agent(
    name="TracerLensAi Agent",
    description="An advanced causal reasoning agent capable of mathematical modeling and deep analysis.",
    model="gemini-2.5-flash",
    instructions=(
        "You are TracerLensAi, an expert AI assistant specializing in causal reasoning, "
        "data analysis, and structural causal models. You can execute Python code to perform "
        "complex mathematical calculations and use Google Search to look up current information. "
        "When asked to perform causal reasoning, identify potential confounders, propose a structural "
        "causal model, and estimate treatment effects step-by-step."
    ),
    # Enable Vertex AI managed tools (MCP)
    tools=["code_execution", "google_search"],

    # Enable the Memory Bank to persist context across sessions
    memory=True,
)

if __name__ == "__main__":
    # Local debugging entrypoint
    agent.run_local()
