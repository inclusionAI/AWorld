from aworld_cli.inner_plugins.smllc.agents.aworld_agent import load_aworld_system_prompt


def test_aworld_prompt_routes_reminders_to_cron():
    prompt = load_aworld_system_prompt()

    assert "For future reminders, delayed execution, or recurring reminders, use `cron`" in prompt
    assert "Do not use `bash` with `sleep`, foreground waiting, delayed `echo`, or temp-file polling to implement reminders." in prompt


def test_aworld_prompt_mentions_examples_for_future_and_recurring_reminders():
    prompt = load_aworld_system_prompt()

    assert "X minutes later remind me" in prompt
    assert "tomorrow remind me" in prompt
    assert "recurring reminders" in prompt


def test_aworld_prompt_documents_cron_tool_for_scheduled_tasks():
    prompt = load_aworld_system_prompt()

    assert "`cron`" in prompt
    assert "Manage scheduled tasks" in prompt
    assert "add: Create a new scheduled task" in prompt


def test_aworld_prompt_allows_passing_raw_reminder_request_to_cron():
    prompt = load_aworld_system_prompt()

    assert "pass the user's raw reminder request directly to `cron`" in prompt
    assert "一分钟后提醒我喝水" in prompt


def test_aworld_prompt_prefers_single_bounded_cron_for_finite_repetition():
    prompt = load_aworld_system_prompt()

    assert "If the user wants a repeating reminder with a fixed total count" in prompt
    assert "single cron task with a run limit" in prompt
    assert "do not create a second stop task" in prompt
