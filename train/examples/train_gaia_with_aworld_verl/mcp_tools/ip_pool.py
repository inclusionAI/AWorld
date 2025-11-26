import os
import traceback

import requests

from aworld.logs.util import logger

# Record used IP addresses
_used_proxies = set()
# Map task_id to real_out_ip for releasing IP after task completion
_task_ip_mapping = {}
# Maximum retry count to avoid infinite loop
_MAX_RETRIES = 100

async def get_proxy_server():
    """
    Get a proxy server IP address.

    Returns:
        Proxy server string in format "ip:port", or None if failed.
    """
    api = f"{os.getenv('IP_POOL_PROXY')}/get_cn_proxy?interval=0&protocol=HTTP"

    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.get(api)
            j = response.json()
            p = j["result"]["data"]
            real_out_ip = p['real_out_ip']
            proxy = f"{p['proxy_public_ip']}:{p['proxy_port']}"

            # Check for duplicates (filter by real_out_ip)
            if real_out_ip in _used_proxies:
                logger.warning(f"Duplicate real_out_ip detected: {real_out_ip} (proxy: {proxy}), retrying... (attempt {attempt + 1}/{_MAX_RETRIES})")
                continue

            # Record new IP (record by real_out_ip)
            _used_proxies.add(real_out_ip)

            # track the mapping for later release
            logger.info(f"Got new proxy: {proxy} (real_out_ip: {real_out_ip})")

            return proxy
        except:
            logger.error(f"Get proxy server error: {traceback.format_exc()}")
            return None

    # If maximum retry count reached without getting a new IP
    logger.error(f"Failed to get a new proxy after {_MAX_RETRIES} attempts, all proxies seem to be duplicates")
    return None


def release_proxy_by_task_id(task_id: str):
    """
    Release the IP address used by a task, making it available for reuse.

    Args:
        task_id: The task ID that was used when getting the proxy.
    """
    if task_id in _task_ip_mapping:
        real_out_ip = _task_ip_mapping.pop(task_id)
        if real_out_ip in _used_proxies:
            _used_proxies.remove(real_out_ip)
            logger.info(f"Released proxy (real_out_ip: {real_out_ip}) for task_id: {task_id}")
        else:
            logger.warning(f"real_out_ip {real_out_ip} not found in _used_proxies when releasing for task_id: {task_id}")
    else:
        logger.warning(f"task_id {task_id} not found in _task_ip_mapping, nothing to release")