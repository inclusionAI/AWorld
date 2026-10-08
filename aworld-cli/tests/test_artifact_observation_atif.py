from __future__ import annotations

import json

from aworld_cli.atif import build_atif_trajectory


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
