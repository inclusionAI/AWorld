from __future__ import annotations

import json
import base64

from mcp.types import CallToolResult, TextContent

from aworld_cli.atif import build_atif_trajectory
from aworld.mcp_client.utils import lower_mcp_call_result
from aworld.sandbox.artifact_observation import (
    artifact_mcp_content,
    observe_artifact_bytes,
)


def test_atif_keeps_artifact_receipt_without_image_payload() -> None:
    encoded_image = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"
    receipt = {
        "schema_version": "aworld.artifact-observation/v1",
        "status": "ready",
        "observation_id": "sha256:observation",
        "mime_type": "image/png",
        "width": 1,
        "height": 1,
        "byte_count": 68,
        "content_sha256": "sha256:content",
    }
    trajectory = build_atif_trajectory(
        {
            "trajectory": [
                {
                    "meta": {
                        "task_id": "task-1",
                        "agent_id": "Aworld",
                        "step": 1,
                    },
                    "action": {
                        "content": "observe",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "observe_artifact",
                                    "arguments": '{"path":"pixel.png"}',
                                },
                            }
                        ],
                    },
                },
                {
                    "meta": {
                        "task_id": "task-1",
                        "agent_id": "Aworld",
                        "step": 2,
                    },
                    "state": {
                        "input": {
                            "action_result": [
                                {
                                    "tool_call_id": "call-1",
                                    "content": json.dumps(receipt),
                                    "metadata": {
                                        "artifact_observation": receipt,
                                        "artifact_observation_ref": (
                                            "aworld-artifact://observation/ref-1"
                                        ),
                                    },
                                }
                            ]
                        }
                    },
                    "action": {"content": "inspected", "tool_calls": []},
                },
            ]
        },
        prompt="inspect",
        agent_name="Aworld",
        agent_version="dev",
    )

    serialized = json.dumps(trajectory, ensure_ascii=False)
    assert "aworld.artifact-observation/v1" in serialized
    assert encoded_image not in serialized
    assert "data:image" not in serialized
    assert "aworld-artifact://" not in serialized


def test_atif_rejects_adversarial_artifact_contract_payloads() -> None:
    image = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4"
        "z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
    )
    observed = observe_artifact_bytes(
        image,
        suffix=".png",
        path_key="sha256:path",
        file_epoch="sha256:" + ("0" * 64),
        framework_scope={"tool_call_id": "call-1"},
    )
    sentinel = "data:image/png;base64,PRIVATE-ATIF-SENTINEL"
    blocks = artifact_mcp_content(observed)
    blocks.append(TextContent(type="text", text=sentinel))
    result = lower_mcp_call_result(
        CallToolResult(content=blocks),
        server_name="terminal",
        tool_name="observe_artifact",
        trusted_artifact_observation=True,
    )
    result.tool_call_id = "call-1"
    trajectory = build_atif_trajectory(
        {
            "trajectory": [
                {
                    "meta": {"task_id": "task-1", "agent_id": "Aworld", "step": 1},
                    "action": {
                        "content": "observe",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "function": {
                                    "name": "observe_artifact",
                                    "arguments": '{"path":"pixel.png"}',
                                },
                            }
                        ],
                    },
                },
                {
                    "meta": {"task_id": "task-1", "agent_id": "Aworld", "step": 2},
                    "state": {
                        "input": {"action_result": [result.model_dump(mode="json")]}
                    },
                    "action": {"content": "failed safely", "tool_calls": []},
                },
            ]
        },
        prompt="inspect",
        agent_name="Aworld",
        agent_version="dev",
    )
    serialized = json.dumps(trajectory, ensure_ascii=False)

    assert sentinel not in serialized
    assert base64.b64encode(image).decode("ascii") not in serialized
    assert "artifact_observation_contract_invalid" in serialized
