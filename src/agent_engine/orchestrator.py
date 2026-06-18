import logging
import json
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
            "Be concise and professional."
        )

    async def process_inquiry(self, user_id: str, prompt: str, session_history: list) -> Dict[str, Any]:
        """
        Main entry point for processing a user inquiry.
        """
        logger.info(f"Processing inquiry for user {user_id}: {prompt}")
        
        # Step 1: LLM Generation
        response = await self.fallback_manager.generate_with_fallback(
            prompt=prompt,
            system_instruction=self.system_instruction
        )
        
        # Step 2: Tool Execution loop (simplified)
        if response.tool_calls:
            logger.info(f"LLM requested tools: {response.tool_calls}")
            # In a real system, you'd loop through and execute tools, then feed results back to LLM.
            # Here we just mock the execution.
            for tool_call in response.tool_calls:
                func_name = tool_call.get("name")
                if func_name in TOOL_REGISTRY:
                    tool_result = await TOOL_REGISTRY[func_name](**tool_call.get("args", {}))
                    # Append result to context and re-prompt (omitted for brevity)
                    response.content += f"\n[Tool {func_name} executed. Result: {json.dumps(tool_result)}]"
        
        # Step 3: Policy Enforcement
        escalate = RoutingPolicy.requires_human_escalation(prompt, response.content)
        
        if escalate:
            response.content = "I have escalated your request to a human agent. They will be with you shortly."
            
        return {
            "response": response.content,
            "escalated": escalate,
            "metrics": {
                "provider": response.provider,
                "model": response.model_name,
                "latency_ms": response.latency_ms,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens
            }
        }
