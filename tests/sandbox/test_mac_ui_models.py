from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.models import (
    ClickRequest,
    SeeTarget,
)


def test_click_request_accepts_target_or_coordinates():
    req = ClickRequest(target_id="B1")
    assert req.target_id == "B1"
    assert req.x is None and req.y is None

    req2 = ClickRequest(x=10, y=20)
    assert req2.target_id is None
    assert req2.x == 10 and req2.y == 20


def test_see_target_requires_target_id():
    target = SeeTarget(target_id="B1", role="button", text="Play")
    assert target.target_id == "B1"
    assert target.role == "button"
    assert target.text == "Play"
