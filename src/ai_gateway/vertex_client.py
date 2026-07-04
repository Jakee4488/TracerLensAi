import time
import asyncio

try:
    from google.cloud import aiplatform
    from vertexai.generative_models import GenerativeModel
    HAVE_VERTEX = True
except ImportError:
    HAVE_VERTEX = False

from src.ai_gateway.interface import AgentResponse

class VertexAIClient:
    def __init__(self, project_id: str, location: str):
        self.project_id = project_id
        self.location = location
        self.models = {}

        if HAVE_VERTEX:
            aiplatform.init(project=project_id, location=location)
            self.models = {
                "gemini-2.5-flash": GenerativeModel("gemini-2.5-flash"),
                "gemini-2.5-pro": GenerativeModel("gemini-2.5-pro"),
                "gemini-1.5-flash-002": GenerativeModel("gemini-1.5-flash-002"),
                "gemini-1.5-pro-002": GenerativeModel("gemini-1.5-pro-002")
            }

    async def generate_response(
        self,
        prompt: str,
        system_instruction: str,
        model_name: str = "gemini-2.5-flash",
        temperature: float = 0.2
    ) -> AgentResponse:
        """
        Calls the Vertex AI API and formats the response.
        """
        if not HAVE_VERTEX:
            await asyncio.sleep(0.8)  # simulate latency
            if "profile" in prompt.lower():
                return AgentResponse(
                    content="",
                    provider="mock-ai",
                    model_name="mock-gemini",
                    prompt_tokens=42,
                    completion_tokens=15,
                    latency_ms=800,
                    tool_calls=[{"name": "fetch_user_profile", "args": {"user_id": "demo"}}]
                )
            else:
                return AgentResponse(
                    content=f"This is a mocked AI response to: {prompt}",
                    provider="mock-ai",
                    model_name="mock-gemini",
                    prompt_tokens=42,
                    completion_tokens=85,
                    latency_ms=800
                )

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

            # Extract tokens safely from _raw_response
            p_tokens = 0
            c_tokens = 0
            if hasattr(response, '_raw_response') and hasattr(response._raw_response, 'usage_metadata'):
                meta = response._raw_response.usage_metadata
                p_tokens = getattr(meta, 'prompt_token_count', 0)
                c_tokens = getattr(meta, 'candidates_token_count', 0)

            return AgentResponse(
                content=response.text,
                provider="vertex-ai",
                model_name=model_name,
                prompt_tokens=p_tokens,
                completion_tokens=c_tokens,
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
