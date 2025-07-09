import uuid
import asyncio
import random
import traceback
import pytest

from tests.base_test import BaseTest
from aworld.runners.state_manager import EventRuntimeStateManager, RunNodeStatus
from aworld.core.event.base import Message


@pytest.mark.asyncio
async def test_create():
    print(f"start test_create")

    try:
        state_manager: EventRuntimeStateManager = EventRuntimeStateManager.instance()
    except Exception as e:
        traceback.print_exc()

    print(f"========create message")
    root_message_id1 = uuid.uuid4().hex
    root_message_id2 = uuid.uuid4().hex
    root_message_id3 = uuid.uuid4().hex

    headers = {
        "session_id": "session1",
        "group_id": "test_group"
    }

    def get_headers(root_message_id):
        return {
            "root_message_id": root_message_id,
            **headers
        }

    sub_node_message1 = Message(
        id=root_message_id1,
        session_id="session1",
        topic="test_topic",
        headers=get_headers(root_message_id1)
    )
    sub_node_message2 = Message(
        id=root_message_id2,
        session_id="session1",
        topic="test_topic",
        headers=get_headers(root_message_id2)
    )
    sub_node_message3 = Message(
        session_id="session1",
        topic="test_topic",
        headers=get_headers(root_message_id3)
    )

    sub_tasks = []

    async def sub_group_task(message: Message):
        await asyncio.sleep(random.randint(1, 3))
        state_manager.start_message_node(message)
        await asyncio.sleep(random.randint(1, 3))
        result_message = Message(
            session_id="session1",
            topic="test_topic",
            headers=headers
        )
        state_manager.save_message_handle_result("sub_node_message1", message, result_message)
        state_manager.end_message_node(message)

    print(f"start create sub group")

    sub_tasks.append(asyncio.create_task(sub_group_task(sub_node_message1)))
    sub_tasks.append(asyncio.create_task(sub_group_task(sub_node_message2)))
    sub_tasks.append(asyncio.create_task(sub_group_task(sub_node_message3)))

    state_manager.create_group(
        group_id=headers["group_id"],
        session_id=headers["session_id"],
        root_node_ids=[root_message_id1, root_message_id2, root_message_id3],
        parent_group_id="test_parant_group"
    )
    print(f"create group complete")
    await asyncio.gather(*sub_tasks)

    group = state_manager.get_group(headers["group_id"])
    assert group is not None
    assert group.status == RunNodeStatus.SUCCESS

    group_detail = state_manager.query_group_detail(headers["group_id"])
    assert group_detail is not None
    for subgroup in group_detail.sub_groups:
        assert subgroup.status == RunNodeStatus.SUCCESS
