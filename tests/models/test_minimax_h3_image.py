import asyncio
from unittest.mock import Mock

import pytest
import requests
from aiohttp import web
from aiohttp.test_utils import TestServer

from aworld.agents.image_agent import ImageAgent
from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.common import Observation
from aworld.models.image_provider import ImageProvider


@pytest.mark.parametrize("status", [200, 422])
def test_h3_image_agent_uses_one_generation_request(status, tmp_path, monkeypatch):
    received = []
    failed = {"error": {"code": "1027", "message": "output image failed content safety check"}}

    async def generate(request):
        received.append(await request.json())
        assert request.headers["Authorization"] == "Bearer example"
        if status == 422:
            return web.json_response(
                {"error": {"type": "invalid_request_error", "message": "no image generated",
                           "http_code": "422"}, "data": [failed, failed]},
                status=422,
            )
        return web.json_response({"data": [failed, {"b64_json": "aGVsbG8=", "size": "1024x1024"}]})

    async def run():
        app = web.Application()
        app.router.add_post("/v1/images/generations", generate)
        async with TestServer(app) as server:
            base_url = str(server.make_url("/")).rstrip("/")
            monkeypatch.setenv("TEXT_TO_IMAGE_BASE_URL", base_url)
            monkeypatch.setenv("TEXT_TO_IMAGE_MODEL_NAME", "MiniMax-H3-Image")
            monkeypatch.setenv("TEXT_TO_IMAGE_API_KEY", "example")
            model = ModelConfig(llm_provider="image", llm_model_name="MiniMax-H3-Image",
                                llm_api_key="example", llm_base_url=base_url)
            agent = ImageAgent(name=f"h3-{status}", conf=AgentConfig(llm_config=model))
            path = tmp_path / "image.png"
            result, = await agent.async_policy(Observation(
                content="draw a pear", info={"output_path": str(path), "n": 2, "aspect_ratio": "3:2"},
            ))
            assert agent.llm.provider.async_provider.timeout >= 600
            if status == 422:
                assert "422" in result.policy_info
                assert not path.exists()
            else:
                assert result.policy_info["status"] == "success"
                assert path.read_bytes() == b"hello"

    asyncio.run(run())
    assert received == [{"model": "MiniMax-H3-Image", "prompt": "draw a pear",
                         "size": "auto", "aspect_ratio": "3:2", "response_format": "b64_json",
                         "output_format": "png", "n": 2}]


@pytest.mark.parametrize("region", ["io", "cn"])
def test_h3_highspeed_url_response(region, monkeypatch):
    provider = ImageProvider(api_key="example", model_name="MiniMax-H3-Image-Highspeed",
                             base_url=f"https://api.minimax.{region}", timeout=900)
    response = Mock(spec=requests.Response)
    response.json.return_value = {"data": [{"url": "https://example.com/pear.png"}]}
    post = Mock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    options = {"aigc_watermark": True} if region == "cn" else {}
    result = provider.generate_image("draw a pear", response_format="url", area="1K", **options)
    assert result.image_url == "https://example.com/pear.png"
    post.assert_called_once()
    assert post.call_args.args == (f"https://api.minimax.{region}/v1/images/generations",)
    assert post.call_args.kwargs["timeout"] == 900
    payload = post.call_args.kwargs["json"]
    assert payload["size"] == "auto"
    assert payload["area"] == "1K"
    assert payload["model"] == "MiniMax-H3-Image-Highspeed"
    assert payload.get("aigc_watermark") == options.get("aigc_watermark")


def test_h3_rejects_conflicting_size_before_generation(monkeypatch):
    provider = ImageProvider(api_key="example", model_name="MiniMax-H3-Image")
    post = Mock()
    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(ValueError, match="Use size='auto'"):
        provider.generate_image("draw a pear", size="1024x1024", area="1K")
    post.assert_not_called()
