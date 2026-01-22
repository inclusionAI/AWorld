import asyncio
import logging

from env_channel import EnvChannelMessage, EnvChannelSubscriber, env_channel_sub

token = "*"
_ws_headers = (
    {"Authorization": f"Bearer {token}"}
)



async def main():
    @env_channel_sub(
        server_url="ws://localhost:8765/channel",
        topics=["demo-channel-new"],
        auto_connect=True,
        auto_reconnect=True,
        reconnect_interval=10.0,
        headers=_ws_headers,
        auto_start=True  # (default): Automatically start subscription thread after module import
    )
    async def handle_demo(msg: EnvChannelMessage):
        logging.info("2decorator received: %s", msg.message)
        print("decorator received: %s", msg.message)
    while True:
        print("waiting...")
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
