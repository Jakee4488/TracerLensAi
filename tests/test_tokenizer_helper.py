from src.ai_gateway.tokenizer_helper import count_tokens


class DummyClient:
    def __init__(self):
        self.calls = 0
        self.default_model_name = "gemini-2.5-flash"

    def count_tokens_for_model(self, text: str, model_name: str) -> int:
        self.calls += 1
        assert model_name == self.default_model_name
        return len(text.split()) + 10


def test_count_tokens_uses_client_when_available():
    client = DummyClient()

    first_count, first_fallback = count_tokens("hello there world", client=client)
    second_count, second_fallback = count_tokens("hello there world", client=client)

    assert first_count == 13
    assert second_count == 13
    assert first_fallback is False
    assert second_fallback is False
    assert client.calls == 1


def test_count_tokens_falls_back_without_client():
    count, fallback_used = count_tokens("hello there world")

    assert count > 0
    assert fallback_used is True