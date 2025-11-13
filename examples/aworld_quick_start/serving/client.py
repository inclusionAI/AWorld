# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import uuid
import httpx

from a2a.client import A2AGrpcClient, A2ACardResolver
from a2a.types import MessageSendParams, Message, Role, Part, TextPart, AgentCard, SendMessageRequest
from a2a.utils import proto_utils

from aworld.logs.util import logger


async def client():
    from a2a.client import A2ACardResolver, A2AClient

    httpx_client = httpx.AsyncClient()

    agent_card: AgentCard = await A2ACardResolver(
        httpx_client,
        base_url=f"http://localhost:12345",
    ).get_agent_card(http_kwargs=None)
    print(agent_card)

    client = A2AClient(httpx_client=httpx_client, agent_card=agent_card)
    send_message_payload = {
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": "What time is it?"}],
            "messageId": uuid.uuid4().hex,
        },
    }
    request = SendMessageRequest(
        id=uuid.uuid4().hex, params=MessageSendParams(**send_message_payload)
    )
    response = await client.send_message(request, http_kwargs={"timeout": 30.0})
    # Close the httpx client when done
    await httpx_client.aclose()

    print(response.model_dump_json(indent=2))


# client util
# response parse util
async def grpc_client():
    import grpc
    from a2a.grpc import a2a_pb2, a2a_pb2_grpc

    httpx_client = httpx.AsyncClient()

    agent_card: AgentCard = await A2ACardResolver(
        httpx_client,
        base_url=f"http://localhost:12345",
    ).get_agent_card(http_kwargs=None)

    logger.info(f'Successfully fetched agent card: {agent_card}')

    async with grpc.aio.insecure_channel(agent_card.url) as channel:
        stub = a2a_pb2_grpc.A2AServiceStub(channel)
        try:
            if agent_card.supports_authenticated_extended_card:
                logger.info('Attempting to fetch authenticated agent card from grpc endpoint')
                proto_card = await stub.GetAgentCard(a2a_pb2.GetAgentCardRequest())
                final_agent_card_to_use = proto_utils.FromProto.agent_card(
                    proto_card
                )
            else:
                final_agent_card_to_use = agent_card
        except Exception:
            logger.exception('Failed to get authenticated agent card. Exiting.')
            return

        logger.info(f'Successfully fetched agent card: {final_agent_card_to_use}')
        client = A2AGrpcClient(stub, agent_card=final_agent_card_to_use)

        request = MessageSendParams(message=Message(
            role=Role.user,
            parts=[Part(root=TextPart(text='roll a 5 sided dice'))],
            message_id=str(uuid.uuid4()),
        ))

        stream_response = client.send_message_streaming(request)
        async for chunk in stream_response:
            logger.info(f"chunk... {chunk.model_dump(mode='json', exclude_none=True)}")


if __name__ == "__main__":
    # asyncio.run(client())
    asyncio.run(grpc_client())
