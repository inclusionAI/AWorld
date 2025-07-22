import json
import os.path
from typing import Dict, List

from aworld import import_package
from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.replay_buffer.base import ReplayBuffer, DataRow, ExpMeta, Experience, InMemoryStorage, Storage
from aworld.runners.state_manager import RuntimeStateManager
from aworld.runners.utils import _to_serializable


class EventReplayBuffer(ReplayBuffer):
    '''
    Event replay buffer for storing and sampling data.
    Adds the ability to build DataRow from messages and export data to files.
    '''
    def __init__(
        self,
        storage: Storage = InMemoryStorage()
    ):
        super().__init__(storage)
        self.task_agent_map = {}

    def build_data_row_from_message(self, message: Message) -> DataRow:
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

        state_manager = RuntimeStateManager.instance()
        observation = message.payload
        node = state_manager._find_node(message.id)
        agent_results = []
        for handle_result in node.results:
            result = handle_result.result
            if isinstance(result, Message) and isinstance(result.payload, list):
                agent_results.extend(result.payload)
        messages = self._get_llm_messages_from_memory(message)

        # Build Experience
        exp_data = Experience(
            state=observation,
            actions=agent_results,
            messages=messages
        )
        
        # Build and return DataRow
        return DataRow(exp_meta=exp_meta, exp_data=exp_data, id=id)

    def _get_llm_messages_from_memory(self, message: Message):
        context = message.context
        return context.context_info.get("llm_input", [])

    def export(self, data_rows: List[DataRow], directory: str = None) -> None:
        '''
        Export data rows to a specified file.
        
        Args:
            data_rows (List[DataRow]): List of data rows to export
            filepath (str): Path of the export file
            
        Raises:
            ValueError: When the data rows list is empty or the file path is invalid
        '''
        if not data_rows:
            logger.error("Data rows list cannot be empty")
            return
        if not directory:
            logger.error("File path cannot be empty")
            return

        filepath = os.path.join(directory, "data.json")
        try:
            # Convert data rows to dictionary list
            data_dicts = [_to_serializable(data_row) for data_row in data_rows]
            
            # Write to JSON file
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(data_dicts, f, ensure_ascii=False, indent=2)
                
            logger.info(f"Successfully exported {len(data_rows)} data rows to {os.path.abspath(filepath)}")

            import_package("oss2")
            import oss2


            # Get OSS credentials from environment variables
            enable_oss_export = os.getenv("EXPORT_REPLAY_TRACE_TO_OSS", "false").lower() == "true"
            access_key_id = os.getenv('OSS_ACCESS_KEY_ID')
            access_key_secret = os.getenv('OSS_ACCESS_KEY_SECRET')
            endpoint = os.getenv('OSS_ENDPOINT')
            bucket_name = os.getenv('OSS_BUCKET_NAME')
            bucket = None

            if not all([access_key_id, access_key_secret, endpoint, bucket_name]):
                enable_oss_export = False
                logger.warn("Missing required OSS environment variables")
            else:
                try:
                    # Initialize OSS client
                    auth = oss2.Auth(access_key_id, access_key_secret)
                    bucket = oss2.Bucket(auth, endpoint, bucket_name)
                except Exception as e:
                    enable_oss_export = False
                    logger.warn(
                        f"Failed to initialize OSS client, endpoint: {endpoint}, bucket: {bucket_name}. Error: {str(e)}")

            if enable_oss_export:
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

        except Exception as e:
            logger.error(f"Failed to export data to {filepath}: {str(e)}")
            raise 