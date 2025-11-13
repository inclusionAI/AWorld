import os

import requests
import logging
import traceback

from aworld.logs.util import logger

# 记录已使用的 IP 地址
_used_proxies = set()
# 最大重试次数，避免无限循环
_MAX_RETRIES = 100

async def get_proxy_server():
    api = f"{os.getenv('IP_POOL_PROXY')}/get_cn_proxy?interval=0&protocol=HTTP"
    
    for attempt in range(_MAX_RETRIES):
        try:
            response = requests.get(api)
            j = response.json()
            p = j["result"]["data"]
            proxy = f"{p['proxy_public_ip']}:{p['proxy_port']}"
            
            # 检查是否重复
            if proxy in _used_proxies:
                logger.warning(f"Duplicate proxy detected: {proxy}, retrying... (attempt {attempt + 1}/{_MAX_RETRIES})")
                continue
            
            # 记录新 IP
            _used_proxies.add(proxy)
            logger.info(f"Got new proxy: {proxy}")
            return proxy
        except:
            logger.error(f"Get proxy server error: {traceback.format_exc()}")
            return None
    
    # 如果达到最大重试次数仍未获得新 IP
    logger.error(f"Failed to get a new proxy after {_MAX_RETRIES} attempts, all proxies seem to be duplicates")
    return None