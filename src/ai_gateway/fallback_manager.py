import logging
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
        self.fallback_sequence = ["gemini-2.5-flash", "gemini-2.5-pro"]

    async def generate_with_fallback(self, prompt: str, system_instruction: str) -> AgentResponse:
        last_error = None

        for model_name in self.fallback_sequence:
            logger.info(f"Attempting generation with model: {model_name}")
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

        # If all fail, return a fatal AgentResponse
        return AgentResponse(
            content="I am currently experiencing high volume and cannot process your request. Please try again later.",
            provider="fallback-manager",
            model_name="none",
            error=f"All models in fallback sequence failed. Last error: {last_error}"
        )
