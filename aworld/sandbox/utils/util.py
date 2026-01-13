"""Utility functions for sandbox module."""
import json
from pathlib import Path
from typing import Optional

from aworld.logs.util import logger


def is_url(path: str) -> bool:
    """Check if the given path is a URL.
    
    Args:
        path: Path string to check
        
    Returns:
        True if path is a URL (starts with http:// or https://), False otherwise
    """
    return path.startswith("http://") or path.startswith("https://")


def is_remote_url(location: str) -> bool:
    """Check if the given location is a remote URL.
    
    This function checks if the location is a local path:
    - If it's clearly a local path, return False
    - Otherwise, default to True (assume it's a remote URL)
    
    Args:
        location: Location string to check (can be a path or URL)
        
    Returns:
        True if location is a remote URL, False if it's a local path
    """
    if not location or not isinstance(location, str):
        return False
    
    location = location.strip()
    
    # Check if it's a local path
    # 1. Absolute paths (Unix/Mac): /path/to/file
    if location.startswith("/"):
        return False
    
    # 2. Relative paths: ./path or ../path
    if location.startswith("./") or location.startswith("../"):
        return False
    
    # 3. User home directory: ~/path
    if location.startswith("~/"):
        return False
    
    # 4. Windows absolute paths: C:\path or D:\path
    if len(location) >= 3 and location[1:3] == ":\\":
        return False
    
    # 5. Windows relative paths: .\path or ..\path
    if location.startswith(".\\") or location.startswith("..\\"):
        return False
    
    # 6. File protocol: file://
    if location.startswith("file://"):
        return False
    
    # If none of the above, it's not a local path, so assume it's a remote URL
    return True


def process_registry_url(registry_url: str) -> str:
    """Process registry_url: expand paths, handle directories, and create file if needed.
    
    Args:
        registry_url: Registry URL or local file path
        
    Returns:
        Processed registry URL (expanded and resolved path for local files)
    """
    if not registry_url:
        return registry_url
    
    # Skip if it's a URL
    if is_url(registry_url):
        return registry_url
    
    try:
        # Expand user path (~/workspace -> /Users/username/workspace)
        expanded_path = Path(registry_url).expanduser()
        resolved_path = expanded_path.resolve()
        
        # Case 1: Path exists and is a directory
        if resolved_path.exists() and resolved_path.is_dir():
            # Check if registry.json exists in the directory
            registry_file = resolved_path / "registry.json"
            if registry_file.exists():
                return str(registry_file)
            else:
                # Create registry.json in the directory
                with open(registry_file, 'w', encoding='utf-8') as f:
                    json.dump({}, f, ensure_ascii=False, indent=2)
                logger.info(f"Created registry file: {registry_file}")
                return str(registry_file)
        
        # Case 2: Path doesn't exist or is a file path
        else:
            # If parent directory doesn't exist, create it
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Create file if it doesn't exist
            if not resolved_path.exists():
                with open(resolved_path, 'w', encoding='utf-8') as f:
                    json.dump({}, f, ensure_ascii=False, indent=2)
                logger.info(f"Created registry file: {resolved_path}")
            
            return str(resolved_path)
            
    except Exception as e:
        logger.warning(f"Failed to process registry_url: {e}")
        return registry_url


def ensure_registry_file_exists(registry_url: str) -> str:
    """Ensure the registry file and its directory exist.
    
    Handles three cases:
    1. URL (http:// or https://): Skip file creation, return as is
    2. Directory path: Check for registry.json, create if not exists
    3. File path: Create file and directory if not exists
    
    Args:
        registry_url: Registry URL or local file path
        
    Returns:
        Processed registry URL (expanded path for local files)
    """
    if not registry_url:
        return registry_url
    
    # Skip if it's a URL
    if is_url(registry_url):
        return registry_url
    
    try:
        # Expand user path (~/workspace -> /Users/username/workspace)
        expanded_path = Path(registry_url).expanduser()
        resolved_path = expanded_path.resolve()
        
        # Case 1: Path exists and is a directory
        if resolved_path.exists() and resolved_path.is_dir():
            # Check if registry.json exists in the directory
            registry_file = resolved_path / "registry.json"
            if registry_file.exists():
                # Use existing registry.json
                logger.info(f"Found existing registry file: {registry_file}")
                return str(registry_file)
            else:
                # Create registry.json in the directory
                with open(registry_file, 'w', encoding='utf-8') as f:
                    json.dump({}, f, ensure_ascii=False, indent=2)
                logger.info(f"Created registry file: {registry_file}")
                return str(registry_file)
        
        # Case 2: Path doesn't exist or is a file path
        else:
            # If parent directory doesn't exist, create it
            resolved_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Create file if it doesn't exist
            if not resolved_path.exists():
                with open(resolved_path, 'w', encoding='utf-8') as f:
                    json.dump({}, f, ensure_ascii=False, indent=2)
                logger.info(f"Created registry file: {resolved_path}")
            
            # Return the resolved path
            return str(resolved_path)
            
    except Exception as e:
        logger.warning(f"Failed to ensure registry file exists: {e}")
        return registry_url

