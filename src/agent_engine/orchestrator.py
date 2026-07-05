import logging
import json
import time
from typing import Dict, Any

from src.ai_gateway.fallback_manager import FallbackManager
from src.ai_gateway.tokenizer_helper import count_tokens
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
            "You are a Workflow Simulation Engine. Do NOT fulfill or answer the user's request. "
            "Instead, analyze their prompt, simulate how it would be routed through an enterprise AI system, "
            "estimate the required tools, evaluate the prompt's efficiency, and provide a workflow simulation report. "
            "You MUST format your output as a clean, concise, and to-the-point Markdown document. "
            "Avoid wordy introductions. Focus on direct analysis using lists and tables."
        )

    async def process_inquiry(self, user_id: str, prompt: str, session_history: list) -> Dict[str, Any]:
        """
        Main entry point for processing a user inquiry.
        """
        logger.info(f"Processing inquiry for user {user_id}: {prompt}")
        trace = []
        prompt_tokens, prompt_fallback = count_tokens(prompt, client=self.fallback_manager.client)

        # Trace User Message
        trace.append({
            "step_type": "user_input",
            "description": "User Message Received",
            "tokens": prompt_tokens,
            "latency_ms": 0.0,
            "token_source": "heuristic" if prompt_fallback else "vertex",
            "token_fallback_used": prompt_fallback,
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
            "latency_ms": response.latency_ms,
            "prompt_tokens": response.prompt_tokens,
            "completion_tokens": response.completion_tokens,
            "token_source": "gateway",
            "token_fallback_used": response.provider != "vertex-ai",
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
                    tool_result_text = json.dumps(tool_result)
                    tool_tokens, tool_fallback = count_tokens(
                        tool_result_text,
                        client=self.fallback_manager.client,
                    )

                    trace.append({
                        "step_type": "tool_call",
                        "description": f"Tool Execution: {func_name}",
                        "tokens": tool_tokens,
                        "latency_ms": t_latency,
                        "token_source": "heuristic" if tool_fallback else "vertex",
                        "token_fallback_used": tool_fallback,
                    })

                    response.content += (
                        f"\n<tool_call name=\"{func_name}\">{tool_result_text}</tool_call>"
                    )

            reprompt_tokens, reprompt_fallback = count_tokens(
                response.content,
                client=self.fallback_manager.client,
            )
            trace.append({
                "step_type": "re_prompt",
                "description": "Re-prompt with Tool Results",
                "tokens": reprompt_tokens,
                "latency_ms": 0.0,
                "token_source": "heuristic" if reprompt_fallback else "vertex",
                "token_fallback_used": reprompt_fallback,
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
