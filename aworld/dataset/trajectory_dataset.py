import copy
import json
import os
import traceback
from datetime import datetime
from typing import Any, Dict, List, Optional, Callable, Type, TYPE_CHECKING, Union

from pydantic import Field

from aworld.utils import import_package
from aworld.core.agent.base import is_agent_by_name
from aworld.core.common import ActionModel
from aworld.core.event.base import Message, Constants
from aworld.core.storage.base import Storage
from aworld.core.storage.condition import Condition
from aworld.core.storage.data import Data
from aworld.core.storage.inmemory_store import InmemoryStorage, InmemoryConfig
from aworld.dataset.dataset import Dataset
from aworld.dataset.types import DataRow, Experience, ExpMeta, TrajectoryItem
from aworld.logs.util import logger
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MemoryMessage
from aworld.models.model_response import ModelResponse
from aworld.runners.state_manager import RuntimeStateManager, EventRuntimeStateManager
from aworld.utils.common import get_local_ip, new_instance
from aworld.utils import import_package
from aworld.utils.common import get_local_ip
from aworld.utils.serialized_util import to_serializable

if TYPE_CHECKING:
    from aworld.runners.task_runner import TaskRunner
    from aworld.dataset.trajectory_strategy import TrajectoryStrategy

class TrajectoryDataset(Dataset[Union[DataRow, TrajectoryItem]]):
    # Allow arbitrary (non-pydantic) types like RuntimeStateManager in fields
    model_config = {"arbitrary_types_allowed": True}
    state_manager: RuntimeStateManager = Field(exclude=True)
    task_agent_map: Dict[str, int] = Field(default={}, description="task agent map")
    storage: Optional[Storage[Any]] = Field(default=None, description="Storage for trajectory data")
    enable_storage: bool = Field(default=False, description="Whether to enable storage")
    strategy: Optional[Any] = Field(default=None, description="Trajectory generation strategy")

    def __init__(self, strategy: Optional[Any] = None, **data):
        super().__init__(**data)
        self.set_strategy(strategy)

    def set_strategy(self, strategy: Any):
        """Set the trajectory generation strategy."""
        from aworld.dataset.trajectory_strategy import TrajectoryStrategy, DefaultTrajectoryStrategy

        if strategy is None:
             self.strategy = DefaultTrajectoryStrategy()
        elif isinstance(strategy, str):
            try:
                self.strategy = new_instance(strategy)
            except Exception as e:
                logger.error(f"Failed to load strategy from string {strategy}: {e}")
                self.strategy = DefaultTrajectoryStrategy()
        elif isinstance(strategy, TrajectoryStrategy):
            self.strategy = strategy
        elif isinstance(strategy, type) and issubclass(strategy, TrajectoryStrategy):
            self.strategy = strategy()
        else:
             logger.warning(f"Invalid strategy type: {type(strategy)}, using default.")
             self.strategy = DefaultTrajectoryStrategy()

    async def generate_and_save(self, task_id: str, runner: 'TaskRunner') -> List[Dict[str, Any]]:
        """Generate trajectory using strategy and save to storage."""
        if not self.strategy:
             self.set_strategy(None)

        try:
            trajectory_data = await self.strategy.generate(task_id, runner)
            if trajectory_data:
                 normalized = [
                     item.to_dict() if hasattr(item, "to_dict") else item
                     for item in trajectory_data
                 ]
                 await self.save_task_trajectory(task_id, normalized)
                 return normalized
        except Exception as e:
            logger.error(f"Failed to generate and save trajectory: {e}")
        return []

    async def append_trajectory(self, message: Message, task_id: str = None) -> Optional[TrajectoryItem]:
        """
        Generate trajectory item from message using strategy and append to dataset.
        
        Args:
            message: The source message
            task_id: Optional task id, if not provided, try to get from message
            
        Returns:
            The generated trajectory item dictionary or None
        """
        if not self.strategy:
            self.set_strategy(None)
            
        try:
            # Generate item using strategy
            # Pass state_manager and use_tools_in_prompt if available
            kwargs = {
                'state_manager': self.state_manager,
                'use_tools_in_prompt': getattr(self, 'use_tools_in_prompt', True),
            }
            item = await self.strategy.generate_item(message, state_manager=self.state_manager,
                                                     use_tools_in_prompt=getattr(self, 'use_tools_in_prompt', True))
            
            if item:
                item_dict = item.to_dict() if hasattr(item, "to_dict") else item
                # Save to storage
                target_task_id = task_id or (message.task_id if hasattr(message, 'task_id') else None)
                if target_task_id:
                    await self.save_task_trajectory(target_task_id, [item_dict])
                
                return item_dict
        except Exception as e:
            logger.error(f"Failed to append message to trajectory: {e}")
        return None

    def default_transform(self) -> Callable[[Message], Union[DataRow, TrajectoryItem]]:
        return self.message_to_datarow

    def message_to_datarow(self, message: Message) -> Union[DataRow, TrajectoryItem]:
        '''
        Build DataRow or TrajectoryItem from a message.
        '''
        # If strategy provides DataRow conversion, use it (legacy path)
        if hasattr(self.strategy, 'message_to_datarow'):
            return self.strategy.message_to_datarow(
                message, 
                self.state_manager, 
                getattr(self, 'use_tools_in_prompt', True)
            )

        # Fallback implementation (will be deprecated)
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
        # id = f"{task_agent_id}_{self.task_agent_map[task_agent_id]}_{message.id}_{self.id}"
        id = message.id

        # Build ExpMeta
        exp_meta = ExpMeta(
            task_id=task_id,
            task_name=task_name,
            agent_id=agent_id,
            step=self.task_agent_map[task_agent_id],
            execute_time=message.timestamp,
            pre_agent=pre_agent
        )

        messages = self._get_llm_messages_from_memory(message)

        observation = message.payload
        node = self.state_manager._find_node(message.id)
        # if node is None or not node.results:
        #     logger.warning(f"Node result not found for message id: {message.id}, node: {node}")
        #     return None
        agent_results = []
        ext_info = {}
        if node and node.results:
            for handle_result in node.results:
                result = handle_result.result
                if isinstance(result, Message) and isinstance(result.payload, list):
                    agent_results.extend(result.payload)
                else:
                    if not ext_info.get("agent_results"):
                        ext_info["agent_results"] = []
                    ext_info["agent_results"].append(to_serializable(handle_result))


            def _get_attr_from_action(obj, attr, default=None):
                if isinstance(obj, ActionModel):
                    return getattr(obj, attr)
                elif isinstance(obj, dict) and attr in obj:
                    return obj[attr]
                return default

            # # append assistant message to messages
            # if agent_results:
            #     agent_result = agent_results[0]
            #     content = _get_attr_from_action(agent_result, "policy_info", "")
            #     last_assistant_message = {
            #         "role": "assistant",
            #         "content": content
            #     }
            #     tool_calls = []
            #     for action in agent_results:
            #         tool_call_id = _get_attr_from_action(action, "tool_call_id")
            #         if tool_call_id:
            #             tool_calls.append({
            #                 "id": tool_call_id,
            #                 "type": "function",
            #                 "function": {
            #                     "name": _get_attr_from_action(action, "tool_name"),
            #                     "arguments": json.dumps(_get_attr_from_action(action, "params"), ensure_ascii=False),
            #                 }
            #             })
            #     last_assistant_message["tool_calls"] = tool_calls
            #     messages.append(last_assistant_message)
        else:
            logger.warning(f"Node result not found for message id: {message.id}, node: {node} not finished yet.")

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
        task_id: str,
        state_manager: RuntimeStateManager = None,
        storage: Optional[Storage[DataRow]] = None,
        enable_storage: bool = True,
        extra_transform: Optional[Callable[[Message], DataRow]] = None,
    ) -> "TrajectoryDataset":
        """
        Create TrajectoryDataset from event messages.
        
        Args:
            name: Dataset name
            event_messages: List of event messages to process
            task_id: Task identifier
            state_manager: Runtime state manager instance
            storage: Storage instance for trajectory data. If None and enable_storage is True, 
                    creates default InmemoryStorage
            enable_storage: Whether to enable storage (default: True)
            extra_transform: Optional transform function
            
        Returns:
            TrajectoryDataset instance with processed data
        """
        if not state_manager:
            state_manager = EventRuntimeStateManager.instance()
        
        # Create default InmemoryStorage if storage is not provided
        if storage is None and enable_storage:
            logger.info("No storage provided, creating default InmemoryStorage for trajectory data")
            storage = InmemoryStorage(InmemoryConfig(max_capacity=100000))
        
        data = []
        ds = cls(
            name=name, 
            data=[], 
            state_manager=state_manager,
            storage=storage,
            enable_storage=enable_storage
        )
        
        # Create block for this task's trajectory data
        block_id = f"trajectory_{task_id}"
        if ds.storage and ds.enable_storage:
            try:
                await ds.storage.create_block(block_id, overwrite=True)
                logger.info(f"Created storage block: {block_id}")
            except Exception as e:
                logger.error(f"Failed to create storage block {block_id}: {str(e)}")
        
        if event_messages:
            valid_agent_messages = await cls._filter_replay_messages(event_messages, task_id)
            if valid_agent_messages:
                for msg in valid_agent_messages:
                    data_row = ds.message_to_datarow(msg)
                    if data_row:
                        data.append(data_row)
        
        # Batch store all data rows to storage
        if data and ds.storage and ds.enable_storage:
            await ds._store_batch(data, block_id)
        
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

    def _deprecated_get_llm_messages_from_memory(self, message: Message):
        return self._retrieve_agent_histories(message)
        context = message.context
        messages = []
        for msg in context.context_info.get("llm_input", []):
            messages.append(msg)
        llm_response: ModelResponse = context.context_info.get("llm_output", None)
        if llm_response:
            last_assistant_message = {
                "role": "assistant",
                "content": llm_response.content
            }
            tool_calls = llm_response.tool_calls
            if tool_calls:
                last_assistant_message["tool_calls"] = [tool_call.to_dict() for tool_call in tool_calls]
            messages.append(last_assistant_message)
        return messages

    def _get_llm_messages_from_memory(self, message: Message):
        memory = MemoryFactory.instance()
        histories = memory.get_all(
            filters={
                "agent_id": message.receiver,
                # "session_id": message.session_id,
                "task_id": message.task_id,
                "memory_type": ["init", "message", "summary"]
            })
        messages = []
        if histories:
            for history in histories:
                if isinstance(history, MemoryMessage):
                    messages.append(history.to_openai_message())
                else:
                    if not self.use_tools_in_prompt and history.metadata.get('tool_calls'):
                        messages.append({'role': history.metadata['role'], 'content': history.content,
                                         'tool_calls': [history.metadata['tool_calls']]})
                    else:
                        messages.append({'role': history.metadata['role'], 'content': history.content,
                                         "tool_call_id": history.metadata.get("tool_call_id")})
        return messages

    async def _store_datarow(self, data_row: DataRow, block_id: str) -> bool:
        """
        Store a single DataRow to storage.
        
        Args:
            data_row: The data row to store
            block_id: The block id (typically task_id based)
            
        Returns:
            bool: True if stored successfully, False otherwise
        """
        if self.storage and self.enable_storage:
            try:
                return await self.storage.create_data(
                    data=data_row, 
                    block_id=block_id, 
                    overwrite=True
                )
            except Exception as e:
                logger.error(f"Failed to store data row {data_row.id}: {str(e)}")
                return False
        return False
    
    async def _store_batch(self, data_rows: List[DataRow], block_id: str) -> bool:
        """
        Batch store multiple DataRows to storage.
        
        Args:
            data_rows: List of data rows to store
            block_id: The block id (typically task_id based)
            
        Returns:
            bool: True if all stored successfully, False otherwise
        """
        if self.storage and self.enable_storage and data_rows:
            try:
                logger.info(f"Storing {len(data_rows)} trajectory data rows to storage with block_id: {block_id}")
                return await self.storage.create_datas(
                    data=data_rows, 
                    block_id=block_id, 
                    overwrite=True
                )
            except Exception as e:
                logger.error(f"Failed to batch store data rows: {str(e)}")
                return False
        return False
    
    async def get_stored_data(self, block_id: str = None, condition: Condition = None) -> List[DataRow]:
        """
        Retrieve stored trajectory data from storage.
        
        Args:
            block_id: The block id to query, if None returns all data
            
        Returns:
            List[DataRow]: List of stored data rows
        """
        if self.storage:
            try:
                if block_id:
                    # TODO: Ensure the returned data is DataRow
                    return await self.storage.get_data_items(block_id=block_id)
                else:
                    return await self.storage.select_data(condition)
            except Exception as e:
                logger.error(f"Failed to retrieve stored data: {str(e)}")
                return []
        return []

    async def save_task_trajectory(self, task_id: str, trajectory_steps: List[Dict[str, Any]]):
        """Save task trajectory data (list of steps) to storage.

        Args:
            task_id: The task id.
            trajectory_steps: The list of trajectory steps.
        """
        if not self.storage:
            return

        data_items = []
        for step in trajectory_steps:
            data_id = step.get("id")
            kwargs = {
                "block_id": task_id,
                "value": step
            }
            if data_id:
                kwargs["id"] = data_id
            data_items.append(Data(**kwargs))

        if data_items:
            try:
                await self.storage.create_datas(data_items, block_id=task_id, overwrite=True)
            except Exception as e:
                logger.error(f"Failed to save task trajectory: {str(e)}")

    async def get_task_trajectory(self, task_id: str) -> List[Dict[str, Any]]:
        """Get task trajectory data from storage.

        Args:
            task_id: The task id.

        Returns:
            List[Dict[str, Any]]: The list of trajectory steps.
        """
        if not self.storage:
            return []

        try:
            data_items = await self.storage.get_data_items(block_id=task_id)
            if data_items:
                return [item.value for item in data_items if hasattr(item, "value")]
            return []
        except Exception as e:
            logger.error(f"Failed to get task trajectory: {str(e)}")
            return []

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

async def generate_trajectory_from_strategy(
        task_id: str,
        trajectory_strategy: Type['TrajectoryStrategy'] | None,
        event_runner: 'TaskRunner'
) -> List[Dict[str, Any]] | None:
    """
    Generate trajectory using a custom strategy or default implementation.
    
    Args:
        task_id (str): Task identifier
        trajectory_strategy (Type[TrajectoryStrategy]): Strategy class for trajectory generation.
            If None, uses DefaultTrajectoryStrategy
        event_runner (TaskRunner): Task runner containing event manager and state manager
        
    Returns:
        Optional[List[Dict[str, Any]]]: Generated trajectory data or None
    """
    if not trajectory_strategy:
        # Use default strategy if none provided
        from aworld.dataset.trajectory_strategy import DefaultTrajectoryStrategy
        strategy = DefaultTrajectoryStrategy()
    else:
        # Instantiate the provided strategy class
        try:
            strategy = trajectory_strategy()
        except Exception as e:
            logger.error(f"Failed to instantiate trajectory strategy {trajectory_strategy}: {str(e)}")
            # Fallback to default strategy
            from aworld.dataset.trajectory_strategy import DefaultTrajectoryStrategy
            strategy = DefaultTrajectoryStrategy()
    
    # Use the strategy to generate trajectory
    try:
        trajectory = await strategy.generate(task_id, event_runner)
        return trajectory
    except Exception as e:
        logger.error(f"Failed to generate trajectory: {str(e)}.{traceback.format_exc()}")
        return None

async def generate_trajectory(
    messages: List[Message], 
    task_id: str, 
    state_mng: RuntimeStateManager = None,
    storage: Optional[Storage[DataRow]] = None,
    enable_storage: bool = True
) -> List[Dict[str, Any]] | None:
    """
    Generate trajectory from messages and optionally store to storage.
    
    Args:
        messages: List of event messages
        task_id: Task identifier
        state_mng: Runtime state manager
        storage: Storage instance. If None and enable_storage is True, creates InmemoryStorage
        enable_storage: Whether to enable storage (default: True)
        
    Returns:
        List of trajectory data as dictionaries, or None if failed
    """
    traj_dataset = await TrajectoryDataset.from_messages(
        name=f"{task_id}_trajectory_dataset", 
        event_messages=messages, 
        task_id=task_id, 
        state_manager=state_mng,
        storage=storage,
        enable_storage=enable_storage
    )

    try:
        trajectory_data = traj_dataset.to_json()
        
        if enable_storage and traj_dataset.storage:
            logger.info(f"Trajectory data for task {task_id} stored successfully. "
                       f"Total {len(traj_dataset.data)} data rows")
        
        return trajectory_data
    except Exception as e:
        logger.error(f"Failed to save trajectories: {str(e)}.{traceback.format_exc()}")
        return None

