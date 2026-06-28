import time
import asyncio
from typing import Dict, Any, Optional
from google.cloud import aiplatform
from vertexai.generative_models import GenerativeModel, Part
# In a real enterprise app, you'd also import Claude SDK from Vertex if using Anthropic.
# For simplicity, we are wrapping Vertex Gemini.

from src.ai_gateway.interface import AgentResponse


class VertexAIClient:
    def __init__(self, project_id: str, location: str):
        self.project_id = project_id
        self.location = location
        aiplatform.init(project=project_id, location=location)

        # Pre-initialize models for performance
        self.models = {
            "gemini-3.5-flash": GenerativeModel("gemini-3.5-flash"),
            "gemini-3-pro-image": GenerativeModel("gemini-3-pro-image")
        }

    async def generate_response(
        self,
        prompt: str,
        system_instruction: str,
        model_name: str = "gemini-1.5-flash",
        temperature: float = 0.2
    ) -> AgentResponse:
        """
        Calls the Vertex AI API and formats the response.
        """
        if model_name not in self.models:
            raise ValueError(f"Model {model_name} not supported/initialized.")

        model = self.models[model_name]

        start_time = time.time()

        try:
            # Vertex AI SDK is largely synchronous/blocking under the hood for some methods,
            # but we use asyncio.to_thread to make it non-blocking in a FastAPI context.
            response = await asyncio.to_thread(
                model.generate_content,
                prompt,
                generation_config={"temperature": temperature}
                # System instructions can be added via system_instruction parameter in GenerativeModel instantiation,
                # or manually injected in the prompt if using older SDK versions.
                # Assuming prompt includes context for this demo.
            )

            latency = (time.time() - start_time) * 1000

            return AgentResponse(
                content=response.text,
                provider="vertex-ai",
                model_name=model_name,
                prompt_tokens=response.usage_metadata.prompt_token_count if hasattr(
                    response, 'usage_metadata') else 0,
                completion_tokens=response.usage_metadata.candidates_token_count if hasattr(
                    response, 'usage_metadata') else 0,
                latency_ms=latency
            )
        except Exception as e:
            latency = (time.time() - start_time) * 1000
            return AgentResponse(
                content="",
                provider="vertex-ai",
                model_name=model_name,
                latency_ms=latency,
                error=str(e)
            )
