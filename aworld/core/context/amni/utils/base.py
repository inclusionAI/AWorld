# coding: utf-8
import json
import os
from typing import Any, Dict

from pydantic import BaseModel

from aworld.logs.util import logger
from aworld.output import ArtifactType
from aworld.output.utils import load_workspace
from aworld.core.context.amni.config import get_env_mode


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
    """
    递归地将对象转换为JSON可序列化的格式
    处理Pydantic模型、字典、列表等
    
    Args:
        obj: 要转换的对象
        
    Returns:
        JSON可序列化的对象
    """
    # 如果是Pydantic模型，转换为字典（兼容v1和v2）
    if isinstance(obj, BaseModel):
        if hasattr(obj, 'model_dump'):
            return obj.model_dump()  # Pydantic v2
        elif hasattr(obj, 'dict'):
            return obj.dict()  # Pydantic v1
        else:
            return dict(obj)
    
    # 如果对象有model_dump方法（可能是其他类型的Pydantic兼容对象）
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    elif hasattr(obj, 'dict'):
        return obj.dict()
    
    # 如果是字典，递归处理值
    if isinstance(obj, dict):
        return {key: _convert_to_json_serializable(value) for key, value in obj.items()}
    
    # 如果是列表或元组，递归处理元素
    if isinstance(obj, (list, tuple)):
        return [_convert_to_json_serializable(item) for item in obj]
    
    # 其他类型直接返回（字符串、数字、布尔值、None等）
    return obj

async def load_workspace(context):
    session_id = context.session_id
    if hasattr(context, 'workspace'):
        workspace = context.workspace
    else:
        workspace_type = os.environ.get("WORKSPACE_TYPE", "local")
        workspace_path = os.environ.get("WORKSPACE_PATH", "./data/workspaces")
        workspace = await load_workspace(session_id, workspace_type, workspace_path)
    return workspace

async def get_artifact_data(context, artifact_id) -> Dict:
    session_id = context.session_id
    if os.getenv('MIND_STREAM_DEBUG_MODE', 'false').lower() == 'true' and get_env_mode() == 'dev':
        # 自动创建目录
        file_path = f'{os.environ.get("TRAJ_STORAGE_BASE_PATH", "./")}/{session_id}/{artifact_id}'
        try:
            with open(file_path, 'r') as f:
                return f.read()
        except FileNotFoundError:
            return None

    workspace = await load_workspace(context)
    data = workspace.get_artifact_data(artifact_id)
    return data

async def get_context_artifact_data(context, context_key) -> Dict:
    artifact_id = context.get(context_key)
    return await get_artifact_data(context, artifact_id)

async def save_artifact(context, artifact_id, data, is_retain_id=False):
    session_id = context.session_id
    if os.getenv('MIND_STREAM_DEBUG_MODE', 'false').lower() == 'true' and get_env_mode() == 'dev':
        # 自动创建目录
        file_path = f'{os.environ.get("TRAJ_STORAGE_BASE_PATH", "./")}/{session_id}/{artifact_id}'
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            # 确保写入的是JSON字符串
            if isinstance(data, str):
                # 如果已经是字符串，尝试验证是否为有效的JSON
                try:
                    json.loads(data)  # 验证是否为有效JSON
                    json_str = data
                except (json.JSONDecodeError, ValueError):
                    # 不是有效的JSON字符串，直接写入
                    json_str = data
            elif isinstance(data, (dict, list)):
                # 字典或列表，先转换为JSON可序列化格式，再转换为JSON字符串
                serializable_data = _convert_to_json_serializable(data)
                json_str = json.dumps(serializable_data, ensure_ascii=False)
            else:
                # 其他类型，先尝试转换为JSON可序列化格式
                try:
                    serializable_data = _convert_to_json_serializable(data)
                    json_str = json.dumps(serializable_data, ensure_ascii=False)
                except (TypeError, ValueError):
                    # 如果无法序列化，转换为字符串
                    json_str = str(data)
            f.write(json_str)
            return artifact_id

    workspace = await load_workspace(context)

    # delete existed old html artifact
    await workspace.delete_artifact(artifact_id)

    # 确保content是JSON字符串
    if isinstance(data, str):
        # 如果已经是字符串，尝试验证是否为有效的JSON
        try:
            json.loads(data)  # 验证是否为有效JSON
            content = data
        except (json.JSONDecodeError, ValueError):
            # 不是有效的JSON字符串，直接使用
            content = data
    elif isinstance(data, (dict, list)):
        # 字典或列表，先转换为JSON可序列化格式，再转换为JSON字符串
        serializable_data = _convert_to_json_serializable(data)
        content = json.dumps(serializable_data, ensure_ascii=False)
    else:
        # 其他类型，先尝试转换为JSON可序列化格式
        try:
            serializable_data = _convert_to_json_serializable(data)
            content = json.dumps(serializable_data, ensure_ascii=False)
        except (TypeError, ValueError):
            # 如果无法序列化，转换为字符串
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

def build_artifact_id(context_key, task_id):
    return f"{context_key}_{task_id}"

async def save_context_artifact(context, context_key, data):
    artifact_id = build_artifact_id(context_key, context.task_id)

    await save_artifact(context, artifact_id, data)

    context.put(context_key, artifact_id)
    return artifact_id

# 将id追加到session文件中
async def append_traj_id_to_session_artifact(context, task_id) -> str:
    content = await get_artifact_data(context=context, artifact_id=TrajType.MULTI_TURN_TASK_ID_DATA)
    logger.info(f'append_traj_id_to_session_artifact|save_task_ids|{content} {task_id}')

    if content is None:
        content = task_id
    else:
        # 检查task_id是否已经存在
        existing_ids = [line.strip() for line in content.strip().split('\n') if line.strip()]
        if task_id not in existing_ids:
            content = f'{content}\n{task_id}'

    await save_artifact(context=context, artifact_id=TrajType.MULTI_TURN_TASK_ID_DATA, data=content, is_retain_id=True)
    return content

