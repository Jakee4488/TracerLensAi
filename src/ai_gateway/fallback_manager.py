import logging
import time
from src.ai_gateway.interface import AgentResponse
from src.ai_gateway.vertex_client import VertexAIClient

logger = logging.getLogger(__name__)


class FallbackManager:
    """
    Handles routing between models. If the primary model fails or is rate limited,
    it falls back to secondary models in the defined sequence.
    """

    def __init__(self, vertex_client: VertexAIClient):
        self.client = vertex_client
        # Define the fallback sequence
        self.fallback_sequence = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash-002", "gemini-1.5-pro-002"]
        self.cooldowns = {}  # model_name -> cooldown_expiration_timestamp

    async def generate_with_fallback(self, prompt: str, system_instruction: str) -> AgentResponse:
        last_error = None
        current_time = time.time()

        # Filter out models that are currently on cooldown due to recent errors
        active_sequence = [
            m for m in self.fallback_sequence
            if m not in self.cooldowns or current_time > self.cooldowns[m]
        ]

        # Fallback to full list if everything is on cooldown (avoid deadlock)
        if not active_sequence:
            active_sequence = self.fallback_sequence

        for model_name in active_sequence:
            logger.info(f"Attempting generation with model: {model_name}")
            try:
                response = await self.client.generate_response(
                    prompt=prompt,
                    system_instruction=system_instruction,
                    model_name=model_name
                )

                if response.error is None:
                    return response

                logger.warning(
                    f"Model {model_name} failed: {response.error}. Attempting next model...")
                last_error = response.error
                # Put model on a 60-second cooldown
                self.cooldowns[model_name] = time.time() + 60
            except Exception as e:
                last_error = str(e)
                # Put model on a 60-second cooldown
                self.cooldowns[model_name] = time.time() + 60

        # If all fail, return a fatal AgentResponse
        return AgentResponse(
            content="I am currently experiencing high volume and cannot process your request. Please try again later.",
            provider="fallback-manager",
            model_name="none",
            error=f"All models in fallback sequence failed. Last error: {last_error}"
        )
