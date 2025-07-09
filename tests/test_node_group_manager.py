import uuid
import asyncio
import random
import traceback
import pytest

from tests.base_test import BaseTest
from aworld.runners.state_manager import EventRuntimeStateManager, RunNodeStatus
from aworld.core.event.base import Message


@pytest.mark.asyncio
async def test_node_group_create():
    state_manager: EventRuntimeStateManager = EventRuntimeStateManager.instance()
    await state_manager.create_group(
        group_id="test_group",
        session_id="session1",
        root_node_ids=["root_message_id1", "root_message_id2", "root_message_id3"],
        parent_group_id="test_parant_group"
    )
    group = state_manager.get_group("test_group")
    assert group is not None
    assert group.status == RunNodeStatus.INIT


@pytest.mark.asyncio
async def test_all_proccess():

    state_manager: EventRuntimeStateManager = EventRuntimeStateManager.instance()

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
            headers=message.headers
        )
        state_manager.save_message_handle_result("sub_node_message1", message, result_message)
        state_manager.end_message_node(message)

    sub_tasks.append(asyncio.create_task(sub_group_task(sub_node_message1)))
    sub_tasks.append(asyncio.create_task(sub_group_task(sub_node_message2)))
    sub_tasks.append(asyncio.create_task(sub_group_task(sub_node_message3)))

    await state_manager.create_group(
        group_id=headers["group_id"],
        session_id=headers["session_id"],
        root_node_ids=[root_message_id1, root_message_id2, root_message_id3],
        parent_group_id="test_parant_group"
    )
    print(f"create group complete, group_id: {headers['group_id']}")
    group = state_manager.get_group(headers["group_id"])
    assert group is not None

    await asyncio.gather(*sub_tasks)

    print(f"sub group complete, group_id: {headers['group_id']}")
    group = state_manager.get_group(headers["group_id"])
    assert group is not None
    assert group.status == RunNodeStatus.SUCCESS

    group_detail = state_manager.query_group_detail(headers["group_id"])
    assert group_detail is not None
    for subgroup in group_detail.sub_groups:
        assert subgroup.status == RunNodeStatus.SUCCESS
