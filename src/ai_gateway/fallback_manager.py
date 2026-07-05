import logging
from src.ai_gateway.interface import AgentResponse

logger = logging.getLogger(__name__)

class FallbackManager:
    def __init__(self, client):
        self.client = client
        # Default fallback chain if none provided
        self.fallback_chain = ["gemini-2.5-flash", "gemini-2.5-pro"]

    async def generate_with_fallback(self, prompt: str, system_prompt: str) -> AgentResponse:
        last_response = None
        for model_name in self.fallback_chain:
            try:
                # Call the client interface
                response = await self.client.generate_response(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model_name=model_name
                )

                # Check for explicit application-level errors
                if response.error:
                    logger.warning(f"Model {model_name} returned error: {response.error}. Trying next.")
                    last_response = response
                    continue

                return response

            except Exception as e:
                logger.error(f"Model {model_name} raised exception: {e}. Trying next.")
                last_response = AgentResponse(
                    content="",
                    provider="vertex-ai",
                    model_name=model_name,
                    error=str(e)
                )

        return last_response
