import logging
import os
import random
import traceback

import requests

logger = logging.getLogger(__name__)

async def get_proxy_server():
    api = f"{os.getenv('IP_POOL_PROXY')}/get_third_proxy?international=0"
    try:
        response = requests.get(api)
        j = response.json()
        p = random.choice(j["result"]["data"])
        proxy = f"{p['proxy_public_ip']}:{p['proxy_port']}"
        return proxy
    except:
        logger.error(f"Get proxy server error: {traceback.format_exc()}")
        return None