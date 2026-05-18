from aworld_cli.builtin_agents.smllc.agents.aworld_agent import (
    build_context_config,
    load_aworld_system_prompt,
)


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


def test_aworld_prompt_forbids_inventing_absolute_reminder_timestamps():
    prompt = load_aworld_system_prompt()

    assert "do not generate or guess an absolute `schedule_value` yourself" in prompt
    assert "Do not call `bash`, write Python, or manually compute the current time" in prompt


def test_aworld_prompt_requires_using_cron_result_as_source_of_truth():
    prompt = load_aworld_system_prompt()

    assert "only trust the tool's returned fields such as `success`, `job_id`, `next_run`, and `error`" in prompt
    assert "use the returned `next_run` as the confirmed reminder time" in prompt


def test_aworld_prompt_prefers_mac_ui_automation_for_host_local_macos_app_control():
    prompt = load_aworld_system_prompt()

    assert "When the user wants to operate a macOS app on the same host machine" in prompt
    assert "use the macOS UI automation action tools as the primary tool path" in prompt
    assert "`permissions`, `list_apps`, `launch_app`, `list_windows`, `focus_window`, `see`, `click`, `type`, `press`, and `scroll`" in prompt
    assert "permissions -> list_apps/launch_app -> list_windows/focus_window -> see -> click/type/press/scroll" in prompt


def test_aworld_prompt_forbids_shell_first_fallback_for_host_local_macos_ui_tasks():
    prompt = load_aworld_system_prompt()

    assert "Do not start with `bash`, Python, AppleScript, or ad-hoc screenshots" in prompt
    assert "unless the `mac_ui_automation` path is unavailable or has already failed for a specific reason" in prompt


def test_aworld_prompt_does_not_treat_mac_ui_automation_as_a_single_tool_name():
    prompt = load_aworld_system_prompt()

    assert "`mac_ui_automation` is the server/capability name, not necessarily a single callable tool name" in prompt
    assert "Do not inspect Python modules or shell out just to discover whether those action tools exist." in prompt


def test_aworld_prompt_prefers_current_surface_exploration_before_fallback():
    prompt = load_aworld_system_prompt()

    assert "Once the target application and likely target surface have been reached" in prompt
    assert "prefer continuing exploration on the current surface rather than switching tools or strategies immediately" in prompt


def test_aworld_prompt_requires_bounded_in_app_exploration_before_shell_fallback():
    prompt = load_aworld_system_prompt()

    assert "If the requested content is not yet visible but the current surface still appears relevant" in prompt
    assert "use bounded in-app exploration first" in prompt
    assert "Do not fall back to shell scripts, screenshots, or OCR until the current surface has been explored" in prompt


def test_aworld_context_config_enables_task_grounding_neuron():
    config = build_context_config(debug_mode=True)

    assert "task_grounding" in config.agent_config.neuron_names


def test_aworld_prompt_requires_grounding_authoritative_request_and_finish_validation():
    prompt = load_aworld_system_prompt()

    assert "Before taking action, derive a short internal checklist of the task's fixed constraints" in prompt
    assert "Treat the original user request as the authoritative source of truth for the task goal" in prompt
    assert "Do not silently replace the requested target, entity, date, time window, topic filter, output location, or deliverable" in prompt
    assert "Before declaring success, verify that the result is supported by evidence gathered in the current run" in prompt
    assert "If the evidence conflicts with the user's constraints, treat the task as incomplete" in prompt
