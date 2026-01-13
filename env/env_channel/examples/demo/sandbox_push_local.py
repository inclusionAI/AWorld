import asyncio
import logging
import os
from datetime import datetime

from env_channel import EnvChannelMessage, EnvChannelSubscriber, EnvChannelPublisher

from aworld.sandbox import Sandbox


async def main():
    token = "***"
    _ws_headers = (
        {"Authorization": f"Bearer {token}"}
    )
    publisher = EnvChannelPublisher(
        server_url="ws://localhost:8765/channel",
        auto_connect=True,
        auto_reconnect=True,
        headers=_ws_headers
    )

    while True:
        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        await publisher.publish(
            topic="demo-channel-new",
            message={"text": f" env-channel:{current_time_str}"},
        )
        print("publish message")
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
