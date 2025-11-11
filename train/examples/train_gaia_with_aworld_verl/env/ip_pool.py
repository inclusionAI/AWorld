import os

import requests
import logging
import traceback

logger = logging.getLogger(__name__)

async def get_proxy_server():
    api = f"{os.getenv('IP_POOL_PROXY')}/get_cn_proxy?interval=0&protocol=HTTP"
    try:
        response = requests.get(api)
        j = response.json()
        p = j["result"]["data"]
        proxy = f"{p['proxy_public_ip']}:{p['proxy_port']}"
        return proxy
    except:
        logger.error(f"Get proxy server error: {traceback.format_exc()}")
        return None