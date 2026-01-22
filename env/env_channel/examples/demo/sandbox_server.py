import asyncio
import logging
import os

from env_channel import EnvChannelMessage, EnvChannelSubscriber
from env_channel.server import EnvChannelServer

from aworld.sandbox import Sandbox


async def main():
    token = "*"
    _ws_headers = (
        {"Authorization": f"Bearer {token}"}
    )
    server = EnvChannelServer(host="0.0.0.0", port=8765)
    await server.start()
    print("EnvChannelServer started at ws://0.0.0.0:8765")
    try:
        while True:
            print("waiting server...")
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await server.stop()

if __name__ == "__main__":
    asyncio.run(main())
