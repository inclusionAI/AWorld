from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld_cli.acp.errors import AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT
from aworld_cli.acp.server import AcpStdioServer


def test_normalize_prompt_text_supports_embedded_resource_text() -> None:
    server = AcpStdioServer()

    prompt_text = server._normalize_prompt_text(
        {
            "content": [
                {"type": "text", "text": "Summarize this context"},
                {
                    "type": "resource",
                    "resource": {
                        "text": "Embedded resource text",
                    },
                },
            ]
        }
    )

    assert prompt_text == "Summarize this context\nEmbedded resource text"


def test_normalize_prompt_text_supports_resource_link_reference() -> None:
    server = AcpStdioServer()

    prompt_text = server._normalize_prompt_text(
        {
            "content": [
                {"type": "text", "text": "Use this reference"},
                {
                    "type": "resource_link",
                    "uri": "file:///tmp/notes.md",
                    "title": "notes.md",
                },
            ]
        }
    )

    assert prompt_text == "Use this reference\nResource link: notes.md (file:///tmp/notes.md)"


def test_normalize_prompt_text_rejects_unbridgeable_rich_blocks() -> None:
    server = AcpStdioServer()

    with pytest.raises(ValueError, match=AWORLD_ACP_UNSUPPORTED_PROMPT_CONTENT):
        server._normalize_prompt_text(
            {
                "content": [
                    {
                        "type": "image",
                        "url": "https://example.com/image.png",
                    }
                ]
            }
        )
