from aworld_cli.core.plugin_manager import _redacted_source_location


def test_remote_source_label_drops_credentials_path_and_query() -> None:
    rendered = _redacted_source_location(
        "remote",
        "https://user:secret@example.test:8443/private/path?token=secret#fragment",
    )

    assert rendered == "https://example.test:8443"
    assert "user" not in rendered
    assert "secret" not in rendered
    assert "private" not in rendered


def test_local_source_label_never_contains_the_path() -> None:
    rendered = _redacted_source_location(
        "local",
        "/Users/private/customer-project/agents",
    )

    assert rendered == "<local-source>"
    assert "/Users/private" not in rendered
