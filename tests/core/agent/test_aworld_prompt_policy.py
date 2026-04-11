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
