from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.main import (
    normalize_see_result,
    resolve_click_target,
    validate_action_params,
)


def test_resolve_click_target_prefers_target_id_over_coordinates():
    target = resolve_click_target({"target_id": "B1", "x": 5, "y": 6})
    assert target == {"target_id": "B1"}


def test_resolve_click_target_falls_back_to_coordinates():
    target = resolve_click_target({"x": 5, "y": 6})
    assert target == {"x": 5, "y": 6}


def test_see_result_includes_required_target_fields():
    result = normalize_see_result(
        {
            "targets": [{"target_id": "B1", "role": "button", "text": "Play"}],
            "visible_text": "Episode summary",
        }
    )
    assert "targets" in result
    assert isinstance(result["targets"], list)
    assert result["targets"][0]["target_id"] == "B1"
    assert result["targets"][0]["role"] == "button"
    assert result["targets"][0]["text"] == "Play"


def test_interaction_actions_accept_timeout_override():
    validate_action_params("click", {"target_id": "B1", "timeout_seconds": 10.0})
    validate_action_params("type", {"text": "hello", "timeout_seconds": 10.0})
    validate_action_params("press", {"keys": "enter", "timeout_seconds": 10.0})
    validate_action_params("scroll", {"direction": "down", "amount": 2, "timeout_seconds": 10.0})


def test_focus_window_requires_window_selector():
    try:
        validate_action_params("focus_window", {"app": "Xiaoyuzhou"})
    except ValueError as exc:
        assert "window" in str(exc)
    else:
        raise AssertionError("focus_window should require window_id or window_title")


def test_non_interaction_actions_accept_minimal_phase1_inputs():
    validate_action_params("permissions", {})
    validate_action_params("list_apps", {})
    validate_action_params("list_windows", {"app": "Xiaoyuzhou"})
    validate_action_params("launch_app", {"app": "Xiaoyuzhou"})
    validate_action_params("see", {"app": "Xiaoyuzhou", "include_artifact": True})
