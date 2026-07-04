import logging
import json
import time
from typing import Dict, Any

from src.ai_gateway.fallback_manager import FallbackManager
from src.agent_engine.tools import TOOL_REGISTRY
from src.agent_engine.policies import RoutingPolicy

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """
    Manages the conversational loop, state tracking, tool execution,
    and fallback routing.
    """

    def __init__(self, fallback_manager: FallbackManager):
        self.fallback_manager = fallback_manager

        self.system_instruction = (
            "You are an enterprise AI customer support agent. "
            "You have access to tools. If the user asks for their profile, use fetch_user_profile. "
            "You MUST format your output as a clean, concise, and to-the-point Markdown document. "
            "Avoid wordy introductions and lengthy explanations. Focus on direct answers, "
            "use clean lists or tables where appropriate, and use code blocks for any code. "
            "Ensure the output fits well on the screen and is highly readable."
        )

    async def process_inquiry(self, user_id: str, prompt: str, session_history: list) -> Dict[str, Any]:
        """
        Main entry point for processing a user inquiry.
        """
        logger.info(f"Processing inquiry for user {user_id}: {prompt}")
        trace = []

        # Trace User Message
        trace.append({
            "step_type": "user_input",
            "description": "User Message Received",
            "tokens": len(prompt.split()),  # Rough estimation for prompt tokens
            "latency_ms": 0.0
        })

        # Step 1: LLM Generation
        response = await self.fallback_manager.generate_with_fallback(
            prompt=prompt,
            system_instruction=self.system_instruction
        )

        trace.append({
            "step_type": "llm_call",
            "description": f"LLM Generation ({response.provider}: {response.model_name})",
            "tokens": response.prompt_tokens + response.completion_tokens,
            "latency_ms": response.latency_ms
        })

        # Step 2: Tool Execution loop (simplified)
        if response.tool_calls:
            logger.info(f"LLM requested tools: {response.tool_calls}")
            for tool_call in response.tool_calls:
                func_name = tool_call.get("name")
                if func_name in TOOL_REGISTRY:
                    t_start = time.time()
                    tool_result = await TOOL_REGISTRY[func_name](**tool_call.get("args", {}))
                    t_latency = (time.time() - t_start) * 1000

                    trace.append({
                        "step_type": "tool_call",
                        "description": f"Tool Execution: {func_name}",
                        "tokens": len(str(tool_result).split()),  # Mock token calculation
                        "latency_ms": t_latency
                    })

                    response.content += f"\n[Tool {func_name} executed. Result: {json.dumps(tool_result)}]"

            trace.append({
                "step_type": "re_prompt",
                "description": "Re-prompt with Tool Results",
                "tokens": len(response.content.split()),  # Mock
                "latency_ms": 0.0
            })

        # Step 3: Policy Enforcement
        t_start = time.time()
        escalate = RoutingPolicy.requires_human_escalation(
            prompt, response.content)
        t_latency = (time.time() - t_start) * 1000

        trace.append({
            "step_type": "policy_check",
            "description": "Escalation Policy Check",
            "tokens": 0,
            "latency_ms": t_latency
        })

        if escalate:
            response.content = "I have escalated your request to a human agent. They will be with you shortly."
            trace.append({
                "step_type": "escalation",
                "description": "Escalated to Human",
                "tokens": 0,
                "latency_ms": 0.0
            })

        trace.append({
            "step_type": "response",
            "description": "Response Delivered",
            "tokens": response.completion_tokens,
            "latency_ms": 0.0
        })

        return {
            "response": response.content,
            "escalated": escalate,
            "trace": trace,
            "metrics": {
                "provider": response.provider,
                "model": response.model_name,
                "latency_ms": sum(t["latency_ms"] for t in trace),
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens
            }
        }
