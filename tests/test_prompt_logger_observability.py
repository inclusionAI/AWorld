from aworld.logs import prompt_log
from aworld.logs.prompt_log import PromptLogger


class _FakePromptLogger:
    def __init__(self):
        self.info_messages = []
        self.debug_messages = []

    def info(self, message):
        self.info_messages.append(message)

    def debug(self, message):
        self.debug_messages.append(message)


def test_log_messages_includes_truncated_assistant_content_at_info_level(monkeypatch):
    fake_logger = _FakePromptLogger()
    monkeypatch.setattr(prompt_log, "prompt_logger", fake_logger)

    long_assistant_content = "assistant-history-" + ("x" * 200)

    PromptLogger._log_messages(
        [
            {"role": "user", "content": "first prompt"},
            {"role": "assistant", "content": long_assistant_content},
            {"role": "user", "content": "next prompt"},
        ]
    )

    info_output = "\n".join(fake_logger.info_messages)

    assert "ASSISTANT" in info_output
    assert "assistant-history-" in info_output
    assert "..." in info_output
    assert long_assistant_content not in info_output
