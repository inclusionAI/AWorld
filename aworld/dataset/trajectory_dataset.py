import json
import os
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable

from pydantic import Field

from aworld import import_package
from aworld.core.agent.base import is_agent_by_name
from aworld.core.common import ActionModel
from aworld.core.event.base import Message, Constants
from aworld.dataset.dataset import Dataset
from aworld.dataset.types import DataRow, Experience, ExpMeta
from aworld.logs.util import logger
from aworld.runners.state_manager import RuntimeStateManager, EventRuntimeStateManager
from aworld.utils.common import get_local_ip
from aworld.utils.serialized_util import to_serializable

class TrajectoryDataset(Dataset[DataRow]):
    # Allow arbitrary (non-pydantic) types like RuntimeStateManager in fields
    model_config = {"arbitrary_types_allowed": True}
    state_manager: RuntimeStateManager
    task_agent_map: Dict[str, int] = Field(default={}, description="task agent map")

    def default_transform(self) -> Callable[[Message], DataRow]:
        return self.message_to_datarow

    def message_to_datarow(self, message: Message) -> DataRow:
        '''
        Build DataRow from a message.

        Args:
            message (Dict): Message data containing necessary metadata and experience data

        Returns:
            DataRow: The constructed data row

        Raises:
            ValueError: When the message is missing required fields
        '''
        if not message:
            raise ValueError("Message cannot be empty")

        agent_id = message.receiver
        task_id = message.context.task_id
        task_name = message.context.get_task().name
        pre_agent = message.sender
        task_agent_id = f"{task_id}_{agent_id}"
        if task_agent_id not in self.task_agent_map:
            self.task_agent_map[task_agent_id] = 0
        self.task_agent_map[task_agent_id] += 1
        id = f"{task_agent_id}_{self.task_agent_map[task_agent_id]}"

        # Build ExpMeta
        exp_meta = ExpMeta(
            task_id=task_id,
            task_name=task_name,
            agent_id=agent_id,
            step=self.task_agent_map[task_agent_id],
            execute_time=message.timestamp,
            pre_agent=pre_agent
        )

        observation = message.payload
        node = self.state_manager._find_node(message.id)
        if node is None or not node.results:
            logger.error(f"Node result not found for message id: {message.id}, node: {node}")
            return None
        agent_results = []
        ext_info = {}
        for handle_result in node.results:
            result = handle_result.result
            if isinstance(result, Message) and isinstance(result.payload, list):
                agent_results.extend(result.payload)
            else:
                ext_info["agent_results"] = ext_info.get("agent_results", []).append(handle_result)
        messages = self._get_llm_messages_from_memory(message)

        def _get_attr_from_action(obj, attr, default=None):
            if isinstance(obj, ActionModel):
                return getattr(obj, attr)
            elif isinstance(obj, dict) and attr in obj:
                return obj[attr]
            return default

        # append assistant message to messages
        if agent_results:
            agent_result = agent_results[0]
            content = _get_attr_from_action(agent_result, "policy_info", "")
            last_assistant_message = {
                "role": "assistant",
                "content": content
            }
            tool_calls = []
            for action in agent_results:
                tool_call_id = _get_attr_from_action(action, "tool_call_id")
                if tool_call_id:
                    tool_calls.append({
                        "id": tool_call_id,
                        "type": "function",
                        "function": {
                            "name": _get_attr_from_action(action, "tool_name"),
                            "arguments": json.dumps(_get_attr_from_action(action, "params"), ensure_ascii=False),
                        }
                    })
            last_assistant_message["tool_calls"] = tool_calls
            messages.append(last_assistant_message)

        # Build Experience
        exp_data = Experience(
            state=observation,
            actions=agent_results,
            messages=messages,
            ext_info=ext_info
        )

        # Build and return DataRow
        return DataRow(exp_meta=exp_meta, exp_data=exp_data, id=id)

    @classmethod
    async def from_messages(
        cls,
        *,
        name: str,
        event_messages: List[Message],
        task_id:str,
        state_manager: RuntimeStateManager = None,
        extra_transform: Optional[Callable[[Message], DataRow]] = None,
    ) -> "TrajectoryDataset":
        if not state_manager:
            state_manager = EventRuntimeStateManager.instance()
        data = []
        ds = cls(name=name, data=[], state_manager=state_manager)
        if event_messages:
            valid_agent_messages = await cls._filter_replay_messages(event_messages, task_id)
            if valid_agent_messages:
                for msg in valid_agent_messages:
                    data_row = ds.message_to_datarow(msg)
                    if data_row:
                        data.append(data_row)
        ds.data = data
        if extra_transform is not None:
            ds.transform(extra_transform)  # type: ignore[arg-type]
        return ds

    @staticmethod
    async def _filter_replay_messages(messages: List[Message], task_id: str) -> List[Message]:
        results = []
        logger.info(f"Retrieving agent messages for task: {task_id}")
        for message in messages:
            if message.task_id != task_id or message.category != Constants.AGENT:
                continue
            sender = message.sender
            receiver = message.receiver
            if not sender or not receiver or not is_agent_by_name(receiver):
                continue
            agent_as_tool = message.headers.get("agent_as_tool", False)
            if agent_as_tool:
                continue
            results.append(message)
        return results

    def _get_llm_messages_from_memory(self, message: Message):
        context = message.context
        return context.context_info.get("llm_input", [])

    def to_json(self) -> List[Dict[str, Any]]:
        return [to_serializable(data_row) for data_row in self.data]

    def to_csv(self, path: str):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_json(), f, ensure_ascii=False, indent=2)

    def export(self) -> None:
        '''
        Export data rows to a specified file.

        Args:
            data_rows (List[DataRow]): List of data rows to export
            filepath (str): Path of the export file

        Raises:
            ValueError: When the data rows list is empty or the file path is invalid
        '''
        enable_file_export = os.getenv("EXPORT_REPLAY_FILES", "false").lower() == "true"
        enable_oss_export = os.getenv("EXPORT_REPLAY_TO_OSS", "false").lower() == "true"
        if not enable_file_export and not enable_oss_export:
            return
        data_rows = self.data

        if not data_rows:
            logger.warn("Data rows list cannot be empty")
            return

        try:
            # Convert data rows to dictionary list
            data_dicts = [to_serializable(data_row) for data_row in data_rows]

            timestamp = datetime.now().strftime("%Y%m%d")
            export_dir = os.getenv('REPLAY_EXPORT_DIRECTORY', None)
            replay_dir = os.path.join(export_dir or "./trace_data", timestamp, get_local_ip(), "replays")
            os.makedirs(replay_dir, exist_ok=True)
            filepath = os.path.join(replay_dir, f"task_trajectory_{timestamp}.json")

            if enable_file_export:
                logger.info(f"Exporting {len(data_rows)} data rows to {filepath}")
                # Write to JSON file
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(data_dicts, f, ensure_ascii=False, indent=2)
                logger.info(f"Successfully exported {len(data_rows)} data rows to {os.path.abspath(filepath)}")

            if enable_oss_export:
                logger.info(f"Exporting {len(data_rows)} data rows to oss")
                self.export_to_oss(data_dicts, filepath)
        except Exception as e:
            logger.error(f"Failed to export replay datas: {e}")
            raise

    def export_to_oss(self, datas, filepath):
        import_package("oss2")
        import oss2

        # Get OSS credentials from environment variables
        access_key_id = os.getenv('OSS_ACCESS_KEY_ID')
        access_key_secret = os.getenv('OSS_ACCESS_KEY_SECRET')
        endpoint = os.getenv('OSS_ENDPOINT')
        bucket_name = os.getenv('OSS_BUCKET_NAME')
        bucket = None

        if not all([access_key_id, access_key_secret, endpoint, bucket_name]):
            logger.warn("Missing required OSS environment variables")
            return
        else:
            try:
                # Initialize OSS client
                auth = oss2.Auth(access_key_id, access_key_secret)
                bucket = oss2.Bucket(auth, endpoint, bucket_name)
            except Exception as e:
                logger.warn(
                    f"Failed to initialize OSS client, endpoint: {endpoint}, bucket: {bucket_name}. Error: {str(e)}")
                return

        # Upload to OSS
        try:
            # Get the relative path
            abs_path = os.path.abspath(filepath)
            path_parts = abs_path.split(os.sep)
            if len(path_parts) >= 4:
                # Get the last 4 parts of the path
                relative_path = os.sep.join(path_parts[-4:])
                oss_key = relative_path
            else:
                oss_key = f"replay_buffer/{os.path.basename(filepath)}"
            logger.info(f"Uploading replay datas to OSS: {oss_key}")
            bucket.put_object_from_file(oss_key, filepath)
            logger.info(f"Successfully uploaded {filepath} to OSS: {oss_key}")
        except Exception as e:
            logger.warn(f"Failed to upload {filepath} to OSS: {str(e)}")


async def generate_trajectory(messages: List[Message], task_id: str, state_mng: RuntimeStateManager = None) -> List[Dict[str, Any]] | None:
    traj_dataset = await TrajectoryDataset.from_messages(name=f"{task_id}_trajectory_dataset", event_messages=messages, task_id=task_id, state_manager=state_mng)

    try:
        # todo: add storage
        # data_rows = traj_dataset.data
        # await self.store_batch(data_rows)

        return traj_dataset.to_json()
    except Exception as e:
        logger.error(f"Failed to save trajectories: {str(e)}.{traceback.format_exc()}")
        return None

