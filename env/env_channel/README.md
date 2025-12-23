## env-channel User Guide (Concise Version)

### 1. Installation

#### 1.1 Install from Local Wheel (Recommended)

```bash
cd env-channel
uv build                     # Build wheel package
ls dist/                     # View generated .whl file

# Install in other projects (example)
cd /your/other/project
uv pip install /absolute/path/env-channel/dist/env_channel-0.1.0-py3-none-any.whl
```

#### 1.2 Local Development Mode Installation (Linked)

```bash
cd /your/other/project
uv pip install -e /absolute/path/env-channel
```

---

### 2. Core Concepts

- **EnvChannelServer**: WebSocket server that forwards messages.
- **EnvChannelPublisher**: Publisher that pushes messages to a `topic`.
- **@env_channel_sub**: Subscriber decorator that marks a function as a subscription handler for a `topic`.

---

### 3. Start WebSocket Server (Environment Side)

Simplest example (can be placed in a standalone script or your service startup logic):

```python
import asyncio
from env_channel.server import EnvChannelServer

async def main():
    server = EnvChannelServer(host="0.0.0.0", port=8765)
    await server.start()
    print("EnvChannelServer started at ws://0.0.0.0:8765")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await server.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

> In actual projects, you can refer to `env-channel-demo` to integrate `EnvChannelServer` into FastAPI's lifespan for unified management.

---

### 4. Publish Messages (Publisher)

```python
import asyncio
from env_channel.client import EnvChannelPublisher

async def main():
    publisher = EnvChannelPublisher(
        server_url="ws://localhost:8765",
        auto_connect=True,
        auto_reconnect=True,
    )

    await publisher.publish(
        topic="demo-channel",
        message={"text": "hello env-channel"},
    )

if __name__ == "__main__":
    asyncio.run(main())
```

- **`topic`**: Business channel name, such as `"order-updates"`, `"task-progress"`.
- **`message`**: `dict`, contains business data.

---

### 5. Subscribe to Messages (Subscriber)

#### 5.1 Direct Use of `EnvChannelSubscriber`

```python
import asyncio
import logging
from env_channel.client import EnvChannelSubscriber
from env_channel.common.message import EnvChannelMessage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    sub = EnvChannelSubscriber(
        server_url="ws://localhost:8765",
        auto_connect=True,
        auto_reconnect=True,
        reconnect_interval=10.0,
    )

    async def handle(msg: EnvChannelMessage):
        logger.info("received: %s", msg.message)

    await sub.subscribe(topics=["demo-channel"], handler=handle)
    logger.info("listening on demo-channel...")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await sub.unsubscribe(["demo-channel"])
        await sub.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
```

#### 5.2 Using Decorator `@env_channel_sub` (Zero Boilerplate)

```python
import asyncio
import logging
from env_channel.client import env_channel_sub
from env_channel.common.message import EnvChannelMessage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@env_channel_sub(
    server_url="ws://localhost:8765",
    topics=["demo-channel"],
    auto_connect=True,
    auto_reconnect=True,
    reconnect_interval=10.0,
    # auto_start=True (default): Automatically start subscription thread after module import
)
async def handle_demo(msg: EnvChannelMessage):
    logger.info("decorator received: %s", msg.message)

async def main():
    logger.info("Listening... (Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping...")

if __name__ == "__main__":
    asyncio.run(main())
```

---

### 6. env-channel-demo Quick Start

The project includes `env-channel-demo` with integrated FastAPI + EnvChannelServer + publish + decorator subscription.

1. Start the demo:

```bash
cd env-channel/env-channel-demo/src/env_channel_demo
uv run main.py
```

2. Publish messages via HTTP:

```bash
curl "http://127.0.0.1:8000/publish?text=hello-from-demo"
```

3. You can see subscription logs in the demo console:

```text
Decorator subscriber 111 received: {'text': 'hello-from-demo'}
```

---

### 7. Summary

- **Environment Side**: Start `EnvChannelServer`, use `EnvChannelPublisher.publish(topic, message)` in business code to push messages.
- **Agent / Client Side**: Use `EnvChannelSubscriber` or `@env_channel_sub` to subscribe to the corresponding `topic`, and handle `EnvChannelMessage` in the handler.
