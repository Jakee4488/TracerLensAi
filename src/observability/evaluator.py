import asyncio
import os
import json
from src.ai_gateway.vertex_client import VertexAIClient
from src.ai_gateway.fallback_manager import FallbackManager
from src.agent_engine.orchestrator import AgentOrchestrator

SYNTHETIC_DATA = [
    {"user_id": "user_123", "prompt": "Hi, what is the status of my order 999?"},
    {"user_id": "user_456",
        "prompt": "I want to cancel my account, your service is terrible. I will sue."},
    {"user_id": "user_789", "prompt": "Can you fetch my user profile?"}
]


async def run_evaluation():
    """
    Mock evaluation harness running synthetic data against the agent.
    Requires GOOGLE_CLOUD_PROJECT to be set.
    """
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "demo-project")
    location = os.environ.get("GOOGLE_CLOUD_REGION", "europe-west2")

    try:
        # In a real run, this would authenticate if ADC is present
        client = VertexAIClient(project_id, location)
        fallback_mgr = FallbackManager(client)
        agent = AgentOrchestrator(fallback_mgr)

        print("Starting Evaluation Run...")
        results = []
        for row in SYNTHETIC_DATA:
            print(f"Testing Prompt: '{row['prompt']}'")
            res = await agent.process_inquiry(row["user_id"], row["prompt"], [])

            # Record result
            results.append({
                "prompt": row["prompt"],
                "escalated": res["escalated"],
                "latency_ms": res["metrics"]["latency_ms"],
                "model": res["metrics"]["model"]
            })
            print(
                f"Result: Escalated={res['escalated']}, Latency={res['metrics']['latency_ms']:.2f}ms\n")

        print("Evaluation Summary:")
        print(json.dumps(results, indent=2))

    except Exception as e:
        print(
            f"Evaluation failed (likely missing credentials for mock run): {e}")

if __name__ == "__main__":
    asyncio.run(run_evaluation())
