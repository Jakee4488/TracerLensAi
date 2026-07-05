import math
import re
from functools import lru_cache
from typing import Any, Tuple


_TOKEN_CACHE: dict[tuple[str, str], int] = {}


@lru_cache(maxsize=2048)
def _heuristic_token_count(text: str) -> int:
    compact_text = re.sub(r"\s+", " ", text.strip())
    if not compact_text:
        return 0

    word_like_tokens = re.findall(r"\w+|[^\w\s]", compact_text)
    character_estimate = math.ceil(len(compact_text) / 4)
    return max(1, round((len(word_like_tokens) + character_estimate) / 2))


def count_tokens(text: str, client: Any = None) -> Tuple[int, bool]:
    """Return a token count and whether a heuristic fallback was used."""
    compact_text = re.sub(r"\s+", " ", text.strip())
    if not compact_text:
        return 0, False

    if client is not None and hasattr(client, "count_tokens_for_model"):
        model_name = (
            getattr(client, "default_model_name", None)
            or getattr(client, "model_name", None)
            or getattr(client, "fallback_model_name", None)
        )
        if model_name:
            cache_key = (compact_text, model_name)
            if cache_key in _TOKEN_CACHE:
                return _TOKEN_CACHE[cache_key], False

            try:
                token_count = int(client.count_tokens_for_model(compact_text, model_name))
                _TOKEN_CACHE[cache_key] = token_count
                return token_count, False
            except Exception:
                pass

    return _heuristic_token_count(compact_text), True
