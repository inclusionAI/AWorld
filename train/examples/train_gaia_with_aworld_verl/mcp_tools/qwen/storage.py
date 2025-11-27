# qwen_agent tools storage
import os
import json
from typing import Any, Dict, Optional


class KeyNotExistsError(Exception):
    """Exception raised when a key doesn't exist in storage"""
    pass


class Storage:
    """Simple file-based storage implementation"""

    def __init__(self, config: Dict[str, Any]):
        self.storage_root_path = config.get('storage_root_path', './storage')
        os.makedirs(self.storage_root_path, exist_ok=True)

    def get(self, key: str) -> str:
        """Get value by key"""
        file_path = os.path.join(self.storage_root_path, f"{key}.json")
        if not os.path.exists(file_path):
            raise KeyNotExistsError(f"Key '{key}' not found")

        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()

    def put(self, key: str, value: str) -> None:
        """Put value by key"""
        file_path = os.path.join(self.storage_root_path, f"{key}.json")
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(value)

    def delete(self, key: str) -> None:
        """Delete value by key"""
        file_path = os.path.join(self.storage_root_path, f"{key}.json")
        if os.path.exists(file_path):
            os.remove(file_path)
