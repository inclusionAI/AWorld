from aworld_cli.core.session_restore import restore_session_to_executor
from aworld_cli.core.session_store import CliSessionRecord, CliSessionStore
from aworld_cli.core.session_transcript import CliSessionTranscript
from aworld_cli.executors.local import LocalAgentExecutor
from aworld.agents.llm_agent import LLMAgent
from aworld.core.context.amni import TaskInput


def test_session_transcript_records_turn_and_renders_terminal_view(tmp_path):
    transcript = CliSessionTranscript(root=tmp_path)

    transcript.record_turn(
        session_id="session_test",
        user_input="hello",
        assistant_output="hi there",
        agent_name="Aworld",
        task_id="task_1",
    )

    restored = transcript.render_for_terminal("session_test")

    assert "Previous session transcript" in restored
    assert "You: hello" in restored
    assert "Aworld:" in restored
    assert "hi there" in restored


def test_session_transcript_preserves_repeated_turns(tmp_path):
    transcript = CliSessionTranscript(root=tmp_path)

    for task_id in ("task_1", "task_2"):
        transcript.record_turn(
            session_id="session_test",
            user_input="continue",
            assistant_output="same summary",
            agent_name="Aworld",
            task_id=task_id,
        )

    restored = transcript.render_for_terminal("session_test")
    restored_messages = transcript.render_for_openai_messages("session_test")

    assert restored.count("You: continue") == 2
    assert restored.count("same summary") == 2
    assert restored_messages == [
        {"role": "user", "content": "continue"},
        {"role": "assistant", "content": "same summary"},
        {"role": "user", "content": "continue"},
        {"role": "assistant", "content": "same summary"},
    ]


def test_session_transcript_ignores_dangling_user_with_empty_assistant(tmp_path):
    transcript = CliSessionTranscript(root=tmp_path)
    path = transcript.path_for("session_test")
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"recorded_at":"2026-07-02T00:00:00+00:00","event":"user",'
        '"session_id":"session_test","task_id":"task_empty","content":"old prompt"}\n'
        '{"recorded_at":"2026-07-02T00:00:00+00:00","event":"assistant",'
        '"session_id":"session_test","task_id":"task_empty","agent_name":"Aworld","content":""}\n',
        encoding="utf-8",
    )

    assert transcript.render_for_terminal("session_test") == ""
    assert transcript.render_for_openai_messages("session_test") == []


def test_session_transcript_does_not_record_empty_assistant_turn(tmp_path):
    transcript = CliSessionTranscript(root=tmp_path)

    transcript.record_turn(
        session_id="session_test",
        user_input="old prompt",
        assistant_output="",
        agent_name="Aworld",
        task_id="task_empty",
    )

    assert transcript.render_for_terminal("session_test") == ""
    assert transcript.render_for_openai_messages("session_test") == []


def test_session_transcript_falls_back_to_legacy_history_and_memory(tmp_path):
    history_path = tmp_path / "cli_history.jsonl"
    history_path.write_text(
        '{"display": "first question", "sessionId": "session_test", "timestamp": 1}\n',
        encoding="utf-8",
    )
    memory_path = tmp_path / ".aworld" / "memory" / "sessions" / "session_test.jsonl"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(
        '{"event": "task_completed", "session_id": "session_test", '
        '"task_id": "task_1", "final_answer": "first answer"}\n',
        encoding="utf-8",
    )

    transcript = CliSessionTranscript(root=tmp_path, history_path=history_path)

    restored = transcript.render_for_terminal("session_test")

    assert "Recovered from session history and memory" in restored
    assert "You: first question" in restored
    assert "first answer" in restored


def test_session_transcript_combines_legacy_history_with_new_transcript(tmp_path):
    history_path = tmp_path / "cli_history.jsonl"
    history_path.write_text(
        '{"display": "legacy question", "sessionId": "session_test", "timestamp": 1}\n',
        encoding="utf-8",
    )
    memory_path = tmp_path / ".aworld" / "memory" / "sessions" / "session_test.jsonl"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(
        '{"event": "task_completed", "session_id": "session_test", '
        '"task_id": "task_legacy", "recorded_at": "2026-07-02T00:00:00+00:00", '
        '"final_answer": "legacy answer"}\n',
        encoding="utf-8",
    )
    transcript = CliSessionTranscript(root=tmp_path, history_path=history_path)
    transcript.record_turn(
        session_id="session_test",
        user_input="new question",
        assistant_output="new answer",
        agent_name="Aworld",
        task_id="task_new",
    )

    restored = transcript.render_for_terminal("session_test")

    assert "legacy question" in restored
    assert "legacy answer" in restored
    assert "new question" in restored
    assert "new answer" in restored


def test_session_transcript_dedupes_legacy_prompt_when_transcript_has_same_turn(tmp_path):
    history_path = tmp_path / "cli_history.jsonl"
    history_path.write_text(
        '{"display": "same question", "sessionId": "session_test", "timestamp": 1}\n',
        encoding="utf-8",
    )
    transcript = CliSessionTranscript(root=tmp_path, history_path=history_path)
    transcript.record_turn(
        session_id="session_test",
        user_input="same question",
        assistant_output="same answer",
        agent_name="Aworld",
        task_id="task_new",
    )

    restored = transcript.render_for_terminal("session_test")
    restored_messages = transcript.render_for_openai_messages("session_test")

    assert restored.count("You: same question") == 1
    assert restored_messages == [
        {"role": "user", "content": "same question"},
        {"role": "assistant", "content": "same answer"},
    ]


def test_restore_session_to_executor_marks_transcript_for_replay(tmp_path):
    store = CliSessionStore(root=tmp_path)
    record = store.upsert_session(
        CliSessionRecord(
            session_id="session_test",
            created_at="2026-07-02T00:00:00+00:00",
            updated_at="2026-07-02T00:00:00+00:00",
            cwd=str(tmp_path),
            agent_name="Aworld",
            mode="interactive",
        )
    )
    transcript = CliSessionTranscript(root=tmp_path)
    transcript.record_turn(
        session_id="session_test",
        user_input="old question",
        assistant_output="old answer",
        agent_name="Aworld",
        task_id="task_1",
    )

    class FakeExecutor:
        session_id = "fresh"

    executor = FakeExecutor()

    restore_session_to_executor(
        record=record,
        executor_instance=executor,
        session_store=store,
        current_agent_name="Aworld",
        current_cwd=str(tmp_path),
    )

    replay = getattr(executor, "_aworld_cli_restored_transcript", None)
    assert replay is not None
    assert replay.session_id == "session_test"
    assert "old question" in replay.rendered_text


def test_restore_session_to_executor_marks_messages_for_model_prompt(tmp_path):
    store = CliSessionStore(root=tmp_path)
    record = store.upsert_session(
        CliSessionRecord(
            session_id="session_test",
            created_at="2026-07-02T00:00:00+00:00",
            updated_at="2026-07-02T00:00:00+00:00",
            cwd=str(tmp_path),
            agent_name="Aworld",
            mode="interactive",
        )
    )
    transcript = CliSessionTranscript(root=tmp_path)
    transcript.record_turn(
        session_id="session_test",
        user_input="old question",
        assistant_output="old answer",
        agent_name="Aworld",
        task_id="task_1",
    )

    class FakeExecutor:
        session_id = "fresh"

    executor = FakeExecutor()

    restore_session_to_executor(
        record=record,
        executor_instance=executor,
        session_store=store,
        current_agent_name="Aworld",
        current_cwd=str(tmp_path),
    )

    restored_messages = getattr(executor, "_aworld_cli_restored_messages", None)
    assert restored_messages == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]


def test_local_executor_consumes_restored_messages_once():
    executor = object.__new__(LocalAgentExecutor)
    executor._aworld_cli_restored_messages = [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]

    first = executor._consume_restored_messages()
    second = executor._consume_restored_messages()

    assert first == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]
    assert second == []


def test_llm_agent_inserts_task_input_messages_after_system_message():
    class FakeTaskInput:
        messages = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ]

    class FakeContext:
        task_input_object = FakeTaskInput()

    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "new question"},
    ]

    restored = LLMAgent._prepend_task_input_messages(messages, FakeContext())

    assert restored == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
    ]


def test_llm_agent_inserts_pydantic_task_input_messages():
    task_input = TaskInput(
        session_id="session_test",
        task_id="task_test",
        task_content="new question",
        messages=[
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
        ],
    )

    class FakeContext:
        task_input_object = task_input

    restored = LLMAgent._prepend_task_input_messages(
        [{"role": "system", "content": "system prompt"}, {"role": "user", "content": "new question"}],
        FakeContext(),
    )

    assert restored[1:3] == [
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
    ]
