#!/usr/bin/env python3
"""
Export structured metadata for all skill documentation files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union
from aworld.logs.util import logger

def extract_front_matter(content_lines: List[str]) -> Tuple[Dict[str, Any], int]:
    """
    Extract YAML-like front matter from the provided content lines.

    Args:
        content_lines (List[str]): The content of the markdown file split into lines.

    Returns:
        Tuple[Dict[str, Any], int]: A dictionary containing the parsed front matter key-value pairs
        and the index where the front matter ends. Values can be strings or parsed JSON objects.

    Example:
        >>> extract_front_matter(["---", "name: sample", "---", "body"])
        ({'name': 'sample'}, 3)
        >>> extract_front_matter(["---", "name: sample", 'tool_list: {"ms-playwright": []}', "---", "body"])
        ({'name': 'sample', 'tool_list': {'ms-playwright': []}}, 4)
    """
    front_matter: Dict[str, Any] = {}
    if not content_lines or content_lines[0].strip() != "---":
        return front_matter, 0

    end_index = 1
    while end_index < len(content_lines) and content_lines[end_index].strip() != "---":
        line = content_lines[end_index].strip()
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            
            # Try to parse JSON values (for tool_list and other structured data)
            if key == "tool_list" and value:
                try:
                    front_matter[key] = json.loads(value)
                    logger.debug(f"✅ Successfully parsed tool_list as JSON: {front_matter[key]}")
                except json.JSONDecodeError as e:
                    logger.warning(f"⚠️ Failed to parse tool_list as JSON: {e}, keeping as string")
                    front_matter[key] = value
            else:
                front_matter[key] = value
        end_index += 1

    if end_index >= len(content_lines):
        return front_matter, len(content_lines)

    return front_matter, end_index + 1


def collect_skill_docs(root_path: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
    """
    Collect skill documentation metadata from all subdirectories containing skill.md files.

    Args:
        root_path (Union[str, Path]): Root directory to search for skill documentation files.

    Returns:
        Dict[str, Dict[str, Any]]: Mapping from skill names to metadata containing
        name, description, tool_list (as dict), usage content, and skill_path.

    Example:
        >>> collect_skill_docs(Path("."))
        {'tts': {'name': 'tts', 'desc': '...', 'tool_list': {...}, 'usage': '...', 'skill_path': '...'}}
    """
    results: Dict[str, Dict[str, Any]] = {}
    logger.debug("Starting to collect skill : %s", root_path)
    if isinstance(root_path, str):
        root_dir = Path(root_path).resolve()
    else:
        root_dir = root_path

    for skill_file in root_dir.glob("**/skill.md"):
        logger.debug("Finished collecting skill: %s", skill_file)
        content = skill_file.read_text(encoding="utf-8").splitlines()
        front_matter, body_start = extract_front_matter(content)
        body_lines = content[body_start:]

        usage = "\n".join(body_lines).strip()
        desc = front_matter.get("desc", front_matter.get("description", ""))
        tool_list = front_matter.get("tool_list", {})
        
        # Ensure tool_list is a dict
        if isinstance(tool_list, str):
            logger.warning(f"⚠️ tool_list for skill '{front_matter.get('name', '')}' is still a string, converting to empty dict")
            tool_list = {}

        skill_name = skill_file.parent.name

        results[skill_name] = {
            "name": skill_name,
            "description": desc,
            "tool_list": tool_list,
            "usage": usage,
            "type": front_matter.get("type", ""),
            "active": front_matter.get("active", "False").lower() == "true",
            "skill_path": skill_file.as_posix(),
        }

    logger.info(f"✅ Total skill count: {len(results)} -> {results.keys()}")
    return results


