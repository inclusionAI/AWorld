import streamlit as st
import json
import os
from typing import Dict, List, Tuple, Any

base_dir = os.path.dirname(__file__)


def list_agents():
    agents_dir = os.path.join(base_dir, "agent_deploy")

    if not os.path.exists(agents_dir):
        return []

    try:
        # 列出agents_dir下的所有目录
        agents = []
        for item in os.listdir(agents_dir):
            item_path = os.path.join(agents_dir, item)
            if os.path.isdir(item_path):
                # 这里可以添加读取配置文件的逻辑
                # 暂时返回目录名和空配置
                agents.append(item)
        return agents
    except OSError:
        # 处理权限错误或其他文件系统错误
        pass
