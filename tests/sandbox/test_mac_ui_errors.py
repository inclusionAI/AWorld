from aworld.sandbox.tool_servers.platforms.mac.ui_automation.src.errors import (
    MacUIError,
    error_payload,
)


def test_error_payload_exposes_code_message_and_optional_details():
    payload = error_payload(
        MacUIError(
            code="PERMISSION_MISSING",
            message="Accessibility missing",
            details={"kind": "accessibility"},
        )
    )
    assert payload["code"] == "PERMISSION_MISSING"
    assert payload["message"] == "Accessibility missing"
    assert payload["details"]["kind"] == "accessibility"
