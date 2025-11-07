import base64
import json
import os
from datetime import datetime
from typing import List, Optional

from aworld.agents.llm_agent import Agent
from aworld.core.common import ActionResult
from aworld.core.context.base import Context
from aworld.logs.util import logger


async def mcp_screen_snapshot(agent: Agent, context: Context):
    try:
        # sand_box = Sandbox(mcp_servers=["virtualpc-mcp-server"],
        #                    mcp_config={
        #                        "mcpServers": {
        #                            "virtualpc-mcp-server": {
        #                                "type": "streamable-http",
        #                                "url": "http://mcp.aworldagents.com/vpc/mcp",
        #                                "headers": {
        #                                    "Authorization": f"{os.getenv('MCP_AUTHORIZATION')}",
        #                                    # "MCP_SERVERS": "readweb-server,browseruse-server,documents-csv-server,documents-docx-server,documents-pptx-server,documents-pdf-server,documents-txt-server,download-server,intelligence-code-server,intelligence-think-server,intelligence-guard-server,media-audio-server,media-image-server,media-video-server,parxiv-server,terminal-server,wayback-server,wiki-server,googlesearch-server",
        #
        #                                    # "MCP_SERVERS": "ms-playwright,google-search,e2b-code-server,image-server,audio-server",
        #                                    "MCP_SERVERS": "ms-playwright",
        #                                    # "MCP_SERVERS": "e2b-code-server",
        #                                    "IMAGE_ENV": f"{{\"E2B_API_KEY\":\"{os.getenv('MCP_E2B_API_KEY', '')}\"}}",
        #                                    # Specify environment variable values for tools on the client side, note JSON String structure
        #                                    "IMAGE_VERSION": f"{os.getenv('IMAGE_VERSION', '')}",
        #                                },
        #                                "timeout": 600,
        #                                "sse_read_timeout": 600,
        #                                "client_session_timeout_seconds": 600
        #                            },
        #                        }
        #                    })
        sand_box = agent.sandbox
        result = await sand_box.mcpservers.call_tool(action_list=[
            {
                "tool_name": "virtualpc-mcp-server",
                "action_name": "browser_take_screenshot",
                "params": {
                }
            }
        ],
        task_id=context.task_id,
        session_id=context.session_id,
        context=context)
        return result
    except Exception as e:
        logger.info(f"call_mcp failed {e}")


def parse_and_save_screenshots(
    screen_shot_result: List[ActionResult],
    task_id: Optional[str] = None,
    save_dir: Optional[str] = None
) -> List[str]:
    """
    解析 screen_shot_result 中的图片并保存到文件

    Args:
        screen_shot_result: ActionResult 列表，每个 ActionResult 的 content 字段可能包含图片数据
        task_id: 任务 ID，用于创建保存目录
        save_dir: 保存目录，如果不提供则使用默认目录

    Returns:
        保存的图片文件路径列表
    """
    saved_files = []
    
    if not screen_shot_result or len(screen_shot_result) == 0:
        return saved_files
    
    # 确定保存目录
    if save_dir is None:
        task_id = task_id or "unknown"
        save_dir = os.path.join("logs", "screen_shot", task_id)
    
    os.makedirs(save_dir, exist_ok=True)
    
    for action_result in screen_shot_result:
        if not action_result or not action_result.content:
            continue
        
        content = action_result.content
        
        # 如果 content 是字符串，尝试解析为 JSON 数组
        if isinstance(content, str):
            try:
                # 尝试解析为 JSON 数组
                content_list = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                # 如果不是 JSON，直接检查是否是 base64 图片
                content_list = [content]
        elif isinstance(content, list):
            content_list = content
        else:
            content_list = [content]
        
        # 遍历 content 数组，查找图片数据
        for item in content_list:
            if not isinstance(item, str):
                continue
            
            # 检查是否是 base64 图片数据
            if item.startswith("data:image"):
                # 提取 base64 部分
                base64_data = item.split(",", 1)[1] if "," in item else item
                
                # 确定图片格式
                if "jpeg" in item or "jpg" in item:
                    ext = "jpg"
                elif "png" in item:
                    ext = "png"
                elif "gif" in item:
                    ext = "gif"
                elif "webp" in item:
                    ext = "webp"
                else:
                    ext = "png"  # 默认使用 png
                
                # 解码 base64
                try:
                    image_data = base64.b64decode(base64_data)
                    
                    # 生成文件名（使用时间戳）
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    filename = f"screenshot_{timestamp}.{ext}"
                    filepath = os.path.join(save_dir, filename)
                    
                    # 保存文件
                    with open(filepath, "wb") as f:
                        f.write(image_data)
                    
                    saved_files.append(filepath)
                    logger.info(f"Saved screenshot to {filepath}")
                except Exception as e:
                    logger.warning(f"Failed to decode and save image: {e}")
    
    return saved_files
