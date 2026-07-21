import tiktoken

from aworld.models.utils import num_tokens_from_messages, num_tokens_from_string


def test_unknown_model_token_count_uses_bundled_tokenizer(monkeypatch):
    def fail_remote_encoding_lookup(_name: str):
        raise AssertionError("unknown-model fallback must not load a remote encoding")

    monkeypatch.setattr(tiktoken, "get_encoding", fail_remote_encoding_lookup)

    assert num_tokens_from_string("replay task", model="glm-5.2") > 0
    assert num_tokens_from_messages(
        [{"role": "user", "content": "replay task"}],
        model="glm-5.2",
    ) > 0
