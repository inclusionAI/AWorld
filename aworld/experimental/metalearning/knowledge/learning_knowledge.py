# coding: utf-8

import json
import os
from typing import List, Dict, Optional, Any

from pydantic import BaseModel

from aworld.core.agent.base import AgentFactory
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.base import Context
from aworld.dataset.types import TrajectoryItem
from aworld.logs.util import logger
from aworld.output import ArtifactType
from aworld.output.utils import load_workspace as load_workspace_util


class AgentSnapshot(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    prompt: Optional[str] = None
    definition: Optional[str] = None
    diffs: Optional[str] = None


class LearningKnowledge:

    @staticmethod
    def get_metadata_dict(item: TrajectoryItem) -> dict:
        meta = item.meta
        if hasattr(meta, 'to_dict'):
            return meta.to_dict()
        elif hasattr(meta, 'model_dump'):
            return meta.model_dump()
        return {}

    @staticmethod
    def get_action_dict(item: TrajectoryItem) -> dict:
        action = item.action
        if hasattr(action, 'to_dict'):
            return action.to_dict()
        elif hasattr(action, 'model_dump'):
            return action.model_dump()
        return {}

    @staticmethod
    def get_task_traj_messages(task_traj) -> list:
        if not task_traj:
            return []
        last_item = task_traj[-1]
        if hasattr(last_item, 'state') and hasattr(last_item.state, 'messages'):
            return last_item.state.messages
        elif isinstance(last_item, dict):
            return last_item.get('state', {}).get('messages', [])
        return []

    @staticmethod
    def get_task_traj_metadata(task_traj) -> dict:
        if not task_traj:
            return {}
        last_item = task_traj[-1]
        if hasattr(last_item, 'meta'):
            meta = last_item.meta
            if hasattr(meta, 'to_dict'):
                return meta.to_dict()
            elif hasattr(meta, 'model_dump'):
                return meta.model_dump()
        elif isinstance(last_item, dict):
            meta = last_item.get('meta', {})
            if isinstance(meta, dict):
                return meta
            elif hasattr(meta, 'to_dict'):
                return meta.to_dict()
            elif hasattr(meta, 'model_dump'):
                return meta.model_dump()
        return {}

    @staticmethod
    def get_uniq_agentid_task_trajs(traj_data: List[TrajectoryItem]) -> Dict[str, List[TrajectoryItem]]:
        task_items_map = {}
        for item in traj_data:
            task_id = item.meta.task_id
            if task_id not in task_items_map:
                task_items_map[task_id] = []
            task_items_map[task_id].append(item)

        for task_id, items in task_items_map.items():
            tmp_items = []
            for item in items:
                metadata = LearningKnowledge.get_metadata_dict(item)
                agent_id = metadata.get('agent_id', None)

                if agent_id is None:
                    tmp_items.append(item)
                else:
                    found = False
                    for i, existing_item in enumerate(tmp_items):
                        existing_metadata = LearningKnowledge.get_metadata_dict(existing_item)
                        existing_agent_id = existing_metadata.get('agent_id', None)
                        if existing_agent_id == agent_id:
                            tmp_items[i] = item
                            found = True
                            break

                    if not found:
                        tmp_items.append(item)

            task_items_map[task_id] = tmp_items

        return task_items_map

    @staticmethod
    def convert_to_store_data(traj_data: List[TrajectoryItem]) -> List[dict]:
        task_items_map = LearningKnowledge.get_uniq_agentid_task_trajs(traj_data)

        flattened_items = []
        for task_id, items in task_items_map.items():
            flattened_items.extend(items)

        return [item.to_dict() for item in flattened_items]

    @staticmethod
    def _read_task_relation_data(context: Context) -> Optional[List[Dict]]:
        # Delegate to DagKnowledge to avoid code duplication
        from aworld.experimental.metalearning.knowledge.dag_knowledge import DagKnowledge
        return DagKnowledge.read_task_relation_data(context)

    @staticmethod
    def find_sub_task(edges: List[Dict], source: str) -> List[Dict]:
        # Delegate to DagKnowledge to avoid code duplication
        from aworld.experimental.metalearning.knowledge.dag_knowledge import DagKnowledge
        return DagKnowledge.find_sub_task(edges, source)

    @staticmethod
    def find_current_step_sub_task(edges: List[Dict], source: str, step: int) -> List[Dict]:
        # Delegate to DagKnowledge to avoid code duplication
        from aworld.experimental.metalearning.knowledge.dag_knowledge import DagKnowledge
        return DagKnowledge.find_current_step_sub_task(edges, source, step)

    @staticmethod
    def sort_traj_data_by_edges(traj_data: List[TrajectoryItem], edges: List[Dict]) -> List[TrajectoryItem]:
        # Delegate to DagKnowledge to avoid code duplication
        from aworld.experimental.metalearning.knowledge.dag_knowledge import DagKnowledge
        return DagKnowledge.sort_traj_data_by_edges(traj_data, edges)

    @staticmethod
    def remove_target_agent_traj_and_parents(context: Context, traj_data: List[TrajectoryItem], target_agent_id: str) -> List[TrajectoryItem]:
        # Delegate to DagKnowledge to avoid code duplication
        from aworld.experimental.metalearning.knowledge.dag_knowledge import DagKnowledge
        return DagKnowledge.remove_target_agent_traj_and_parents(
            context, traj_data, target_agent_id, LearningKnowledge.get_metadata_dict
        )

    @staticmethod
    def _extract_tool_call_input(tool_call_id: str, tool_calls: List[Any]) -> str:
        if not tool_call_id or tool_call_id == 'tool' or not tool_calls:
            return ''

        for tc in tool_calls:
            tc_id = tc.get('id') if isinstance(tc, dict) else getattr(tc, 'id', None)
            if tc_id == tool_call_id:
                if isinstance(tc, dict):
                    function = tc.get('function', {})
                    if function:
                        return function.get('arguments', '') or str(tc)
                    return str(tc)
                if hasattr(tc, 'function') and hasattr(tc.function, 'arguments'):
                    return tc.function.arguments or str(tc)
                return str(tc)

        return ''

    @staticmethod
    def parse_traj_to_graph(context: Context, traj_data: List[TrajectoryItem]) -> Dict[str, List]:
        nodes = []
        edges = []

        if not traj_data:
            return {"nodes": nodes, "edges": edges}

        id_counter = {}
        task_index = 0

        edges = LearningKnowledge._read_task_relation_data(context) or []
        traj_data = LearningKnowledge.sort_traj_data_by_edges(traj_data, edges)
        task_items_map = LearningKnowledge.get_uniq_agentid_task_trajs(traj_data)

        for task_id, items in task_items_map.items():
            if not items:
                continue

            msgs = []
            for item in items:
                metadata = LearningKnowledge.get_metadata_dict(item)
                action = LearningKnowledge.get_action_dict(item)
                for message in item.state.messages:
                    msgs.append({'metadata': metadata, 'message': message, 'action': action})

            message_count = len(msgs) if msgs else 0

            current_task_index = task_index
            agent_name = LearningKnowledge.get_metadata_dict(items[0]).get('agent_id', task_id)
            if hasattr(context, 'session_id') and agent_name.endswith(f"_{context.session_id}"):
                agent_name = agent_name[:-len(f"_{context.session_id}")]

            task_node = {
                "id": task_id,
                "label": agent_name,
                "type": "task",
                "task_index": current_task_index,
                "message_count": message_count
            }
            nodes.append(task_node)
            task_index += 1

            user_message_content = ''
            for msg in msgs:
                message = msg.get('message', {})
                if message.get('role') == 'user':
                    user_message_content = message.get('content', '')
                    break

            assistant_count = 0
            last_tool_call_content = ''
            last_assistant_tool_calls = []
            for msg_idx, msg in enumerate(msgs):
                metadata = msg.get('metadata', {})
                action = msg.get('action', {})
                message = msg.get('message', {})

                role = message.get('role', None)
                if role == 'system' or role == 'user':
                    continue

                tool_calls = None
                if role == 'assistant':
                    original_id = metadata.get('agent_id', 'assistant')
                    if assistant_count == 0:
                        input_content = user_message_content
                    else:
                        input_content = last_tool_call_content
                    output = message.get('content', '')
                    tool_calls_list = message.get('tool_calls', [])
                    tool_calls = str(tool_calls_list)
                    last_assistant_tool_calls = tool_calls_list if isinstance(tool_calls_list, list) else []
                    assistant_count += 1
                elif role == 'tool':
                    tool_call_id = message.get('tool_call_id', 'tool')
                    original_id = tool_call_id
                    input_content = LearningKnowledge._extract_tool_call_input(tool_call_id, last_assistant_tool_calls)
                    output = message.get('content', '')
                    last_tool_call_content = output
                    if AgentFactory.agent_instance(original_id) is not None:
                        continue
                else:
                    continue

                if not original_id:
                    continue

                is_sub_task = False
                if role == 'tool' and LearningKnowledge.find_current_step_sub_task(edges, task_id, assistant_count) != []:
                    is_sub_task = True
                    logger.info(f'find related sub task, skip {original_id}')

                if original_id in id_counter:
                    id_counter[original_id] += 1
                    unique_id = f"{original_id}_{id_counter[original_id]}"
                else:
                    id_counter[original_id] = 0
                    unique_id = original_id

                display_label = original_id
                if hasattr(context, 'session_id') and context.session_id and display_label and isinstance(display_label, str) and display_label.endswith(f"_{context.session_id}"):
                    display_label = display_label[:-len(f"_{context.session_id}")]

                message_node = {
                    "id": f"{task_id}_{unique_id}_{msg_idx}",
                    "label": display_label,
                    "input": input_content,
                    "output": output,
                    "tool_call": tool_calls,
                    "type": role,
                    "task_id": task_id,
                    "task_index": current_task_index,
                    "msg_index": msg_idx,
                    "is_sub_task": is_sub_task,
                    "is_agent_finished": action.get('is_agent_finished', True)
                }
                nodes.append(message_node)

                merged_nodes = []
                for idx, node in enumerate(nodes):
                    if node.get('type') == 'tool' and merged_nodes and merged_nodes[-1].get('type') == 'tool':
                        merged_nodes[-1]['label'] = merged_nodes[-1].get('label', '') + '\n' + node.get('label', '')
                        merged_nodes[-1]['input'] = merged_nodes[-1].get('input', '') + '\n' + node.get('input', '')
                        merged_nodes[-1]['output'] = merged_nodes[-1].get('output', '') + '\n' + node.get('output', '')
                    else:
                        merged_nodes.append(node)
                nodes = merged_nodes

        return {"nodes": nodes, "edges": edges}

    @staticmethod
    async def _extract_agents_and_tools_from_item(context: ApplicationContext, item: TrajectoryItem, agents_config: dict):
        # Delegate to MetaKnowledge to avoid code duplication
        from aworld.experimental.metalearning.knowledge.meta_knowledge import MetaKnowledge
        await MetaKnowledge.extract_agents_and_tools_from_item(context, item, agents_config)

    @staticmethod
    async def get_running_meta(context: ApplicationContext, task_id: Optional[str] = None) -> Dict[str, Any]:
        # Delegate to MetaKnowledge to avoid code duplication
        from aworld.experimental.metalearning.knowledge.meta_knowledge import MetaKnowledge
        return await MetaKnowledge.get_running_meta(context, task_id)

    @staticmethod
    async def get_running_exp(context: Context) -> Optional[List[TrajectoryItem]]:
        task_graph = context.get_task_graph()
        if not task_graph or not task_graph.get('nodes'):
            task_traj = await context.get_task_trajectory(context.task_id)
            if not task_traj:
                return None
            return list(task_traj) if isinstance(task_traj, list) else [task_traj]

        all_trajectory_items = []
        for node in task_graph['nodes']:
            tid = node.get('id')
            if tid is None:
                continue
            task_traj = await context.get_task_trajectory(tid)
            if task_traj:
                if isinstance(task_traj, list):
                    all_trajectory_items.extend(task_traj)
                else:
                    all_trajectory_items.append(task_traj)

        return all_trajectory_items if all_trajectory_items else None

    @staticmethod
    async def save_meta(context: Context, swarm_source: str, agents_source: dict[str, str]):
        # Delegate to MetaKnowledge to avoid code duplication
        from aworld.experimental.metalearning.knowledge.meta_knowledge import MetaKnowledge
        await MetaKnowledge.save_meta(context, swarm_source, agents_source)

    @staticmethod
    async def get_saved_meta(context: Context, task_id: str = None) -> Optional[dict]:
        # Delegate to MetaKnowledge to avoid code duplication
        from aworld.experimental.metalearning.knowledge.meta_knowledge import MetaKnowledge
        return await MetaKnowledge.get_saved_meta(context, task_id)

    @staticmethod
    async def get_saved_exp(context: Context, task_id: str = None) -> Optional[List[TrajectoryItem]]:
        data = await get_context_artifact_data(context, TrajType.EXP_DATA, task_id)
        if not data:
            return None

        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception as e:
                logger.warning(f"Failed to load saved exp data: {e}")
                return None

        if isinstance(data, list):
            return [TrajectoryItem(**item) for item in data if isinstance(item, dict)]

        return None


class TrajType:
    EXP_DATA = 'mind_stream_exp_data'
    GRAPH_DATA = 'mind_stream_graph_data'
    META_DATA = 'mind_stream_meta_data'
    MULTI_TURN_TASK_ID_DATA = 'multi_task_id_data'
    META_LEARNING_REPORT_DATA = 'meta_learning_report_data'


class MindStreamType:
    MIND_STREAM_HTML = 'mind_stream_html'
    MIND_STREAM_REMOVED_HTML_URL = 'mind_stream_removed_html_url'
    MIND_STREAM_REMOVED_HTML = 'mind_stream_removed_html'


def _convert_to_json_serializable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        if hasattr(obj, 'model_dump'):
            return obj.model_dump()
        elif hasattr(obj, 'dict'):
            return obj.dict()
        return dict(obj)
    
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    elif hasattr(obj, 'dict'):
        return obj.dict()
    
    if isinstance(obj, dict):
        return {key: _convert_to_json_serializable(value) for key, value in obj.items()}
    
    if isinstance(obj, (list, tuple)):
        return [_convert_to_json_serializable(item) for item in obj]
    
    return obj


async def load_workspace(context):
    session_id = context.session_id
    if hasattr(context, 'workspace'):
        workspace = context.workspace
    else:
        workspace_type = os.environ.get("WORKSPACE_TYPE", "local")
        workspace_path = os.environ.get("WORKSPACE_PATH", "./data/workspaces")
        workspace = await load_workspace_util(session_id, workspace_type, workspace_path)
    return workspace


async def get_artifact_data(context, artifact_id) -> Dict:
    workspace = await load_workspace(context)
    data = workspace.get_artifact_data(artifact_id)
    return data


async def get_context_artifact_data(context, context_key, task_id) -> Dict:
    artifact_id = build_artifact_id(context_key, task_id)
    return await get_artifact_data(context, artifact_id)

async def save_artifact(context, artifact_id, data):
    session_id = context.session_id
    workspace = await load_workspace(context)

    await workspace.delete_artifact(artifact_id)

    if isinstance(data, str):
        try:
            json.loads(data)
            content = data
        except (json.JSONDecodeError, ValueError):
            content = data
    elif isinstance(data, (dict, list)):
        serializable_data = _convert_to_json_serializable(data)
        content = json.dumps(serializable_data, ensure_ascii=False)
    else:
        try:
            serializable_data = _convert_to_json_serializable(data)
            content = json.dumps(serializable_data, ensure_ascii=False)
        except (TypeError, ValueError):
            content = str(data)

    await workspace.create_artifact(
        artifact_type=ArtifactType.HTML,
        artifact_id=artifact_id,
        content=content,
        metadata={
            "session_id": session_id
        }
    )
    return artifact_id


def build_artifact_id(context_key, task_id, session_id=None):
    artifact_id = f"{context_key}_{task_id}"
    
    if session_id is not None:
        base_path = os.environ.get("TRAJ_STORAGE_BASE_PATH", "./")
        return f'{base_path}/{session_id}/{artifact_id}'
    
    return artifact_id


async def save_context_artifact(context, context_key, data):
    artifact_id = build_artifact_id(context_key, context.task_id)

    await save_artifact(context, artifact_id, data)

    context.put(context_key, artifact_id)
    return artifact_id


async def append_traj_id_to_session_artifact(context, task_id) -> str:
    content = await get_artifact_data(context=context, artifact_id=TrajType.MULTI_TURN_TASK_ID_DATA)
    logger.info(f'append_traj_id_to_session_artifact|save_task_ids|{content} {task_id}')

    if content is None:
        content = task_id
    else:
        existing_ids = [line.strip() for line in content.strip().split('\n') if line.strip()]
        if task_id not in existing_ids:
            content = f'{content}\n{task_id}'

    await save_artifact(context=context, artifact_id=TrajType.MULTI_TURN_TASK_ID_DATA, data=content)
    return content

