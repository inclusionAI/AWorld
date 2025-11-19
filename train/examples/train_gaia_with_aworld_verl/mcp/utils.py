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
        sand_box = agent.sandbox
        tool_name = os.getenv("MCP_SERVER", "virtualpc-mcp-server")
        action_name = os.getenv("MCP_NAME", "browser_take_screenshot")
        result = await sand_box.mcpservers.call_tool(action_list=[
            {
                "tool_name": tool_name,
                "action_name": action_name,
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
) -> tuple[List[str], bool]:
    """
    Parse images from screen_shot_result and save to files

    Args:
        screen_shot_result: List of ActionResult, each ActionResult's content field may contain image data
        task_id: Task ID, used to create save directory
        save_dir: Save directory, if not provided, use default directory

    Returns:
        (List of saved image file paths, whether all content is empty)
    """
    saved_files = []
    
    logger.info(f"parse_and_save_screenshots called with task_id={task_id}, save_dir={save_dir}, "
                f"screen_shot_result length={len(screen_shot_result) if screen_shot_result else 0}")
    
    if not screen_shot_result or len(screen_shot_result) == 0:
        logger.error(f"screen_shot_result is empty or None, no screenshots to save. "
                     f"screen_shot_result: {screen_shot_result}")
        return saved_files, True
    
    # Determine save directory
    if save_dir is None:
        task_id = task_id or "unknown"
        save_dir = os.path.join("logs", "screen_shot", task_id)
    
    os.makedirs(save_dir, exist_ok=True)
    logger.info(f"Created/verified save directory: {save_dir}")
    
    empty_content_count = 0
    invalid_item_count = 0
    non_image_item_count = 0
    
    for idx, action_result in enumerate(screen_shot_result):
        if not action_result:
            logger.warning(f"Action result at index {idx} is None, skipping. "
                          f"action_result: {action_result}")
            empty_content_count += 1
            continue
        
        if not action_result.content:
            logger.warning(f"Action result at index {idx} has empty content, skipping. "
                          f"action_result: {action_result}, content: {action_result.content}")
            empty_content_count += 1
            continue
        
        content = action_result.content
        
        # If content is a string, try to parse as JSON array
        if isinstance(content, str):
            try:
                # Try to parse as JSON array
                content_list = json.loads(content)
                logger.debug(f"Parsed content at index {idx} as JSON array with {len(content_list)} items")
            except (json.JSONDecodeError, TypeError):
                # If not JSON, directly check if it's base64 image
                content_list = [content]
                logger.debug(f"Content at index {idx} is not JSON, treating as single string")
        elif isinstance(content, list):
            content_list = content
            logger.debug(f"Content at index {idx} is already a list with {len(content_list)} items")
        else:
            content_list = [content]
            logger.debug(f"Content at index {idx} is of type {type(content)}, converting to list")
        
        # Iterate through content array to find image data
        for item_idx, item in enumerate(content_list):
            if not isinstance(item, str):
                logger.debug(f"Item at index {idx}.{item_idx} is not a string (type: {type(item)}), skipping")
                invalid_item_count += 1
                continue
            
            # Check if it's base64 image data
            if item.startswith("data:image"):
                # Extract base64 part
                base64_data = item.split(",", 1)[1] if "," in item else item
                
                # Determine image format
                if "jpeg" in item or "jpg" in item:
                    ext = "jpg"
                elif "png" in item:
                    ext = "png"
                elif "gif" in item:
                    ext = "gif"
                elif "webp" in item:
                    ext = "webp"
                else:
                    ext = "png"  # Default to png
                
                # Decode base64
                try:
                    image_data = base64.b64decode(base64_data)
                    
                    # Generate filename (using timestamp)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                    filename = f"screenshot_{timestamp}.{ext}"
                    filepath = os.path.join(save_dir, filename)
                    
                    # Save file
                    with open(filepath, "wb") as f:
                        f.write(image_data)
                    
                    saved_files.append(filepath)
                    logger.info(f"Saved screenshot to {filepath} (size: {len(image_data)} bytes)")
                except Exception as e:
                    logger.warning(f"Failed to decode and save image at index {idx}.{item_idx}: {e}")
            else:
                logger.debug(f"Item at index {idx}.{item_idx} is not a base64 image data (starts with: {item[:50] if len(item) > 50 else item}), skipping")
                non_image_item_count += 1
    
    # Determine if all content is empty
    total_items = len(screen_shot_result)
    all_empty = (empty_content_count == total_items and len(saved_files) == 0)
    
    if all_empty:
        logger.error(f"All content is empty! parse_and_save_screenshots completed: saved {len(saved_files)} screenshots, "
                    f"empty content: {empty_content_count}, invalid items: {invalid_item_count}, "
                    f"non-image items: {non_image_item_count}, total items: {total_items}. "
                    f"screen_shot_result: {screen_shot_result}")
    else:
        logger.info(f"parse_and_save_screenshots completed: saved {len(saved_files)} screenshots, "
                    f"empty content: {empty_content_count}, invalid items: {invalid_item_count}, "
                    f"non-image items: {non_image_item_count}")
    
    return saved_files, all_empty
