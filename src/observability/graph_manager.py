import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

class GraphitiContextBuilder:
    """
    Integrates with 'Graphiti' (https://github.com/getzep/graphiti) to dynamically
    build contextual semantic graphs from raw agent traces and prompts before
    passing them to the Causal Estimator.
    """

    def __init__(self):
        logger.info("Initialized GraphitiContextBuilder")

    def extract_graph_features(self, raw_trace: Dict[str, Any]) -> Dict[str, Any]:
        """
        In a real integration, this would call Graphiti to parse the semantic context
        of the conversation/prompt and return structured node/edge features.
        """
        # Mocking Graphiti extraction
        prompt_text = raw_trace.get("prompt", "")

        features = {
            "includes_search_tool": 1 if "search" in prompt_text.lower() else 0,
            "includes_few_shot": 1 if "example:" in prompt_text.lower() else 0,
            "system_prompt_size": len(prompt_text),
            "query_complexity": self._estimate_complexity(prompt_text),
            "total_token_cost": raw_trace.get("total_token_cost", 0)
        }
        return features

    def _estimate_complexity(self, text: str) -> int:
        # Simple mock complexity heuristic
        words = len(text.split())
        if words > 100:
            return 3
        if words > 50:
            return 2
        return 1
