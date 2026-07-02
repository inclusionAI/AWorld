from pathlib import Path

from aworld.models.ant_gpt_image_provider import (
    AntGptImageProvider,
    _GENERIC_CALL_ENDPOINT,
    _METHOD_EDITS,
    _METHOD_GENERATIONS,
)


def test_build_request_uses_generic_call_for_text_to_image():
    provider = AntGptImageProvider(
        api_key="test-key",
        base_url="https://matrixcube.alipay.com",
        model_name="gpt-image-2",
        sync_enabled=False,
        async_enabled=False,
    )

    endpoint, payload, output_format, request_body_type = provider._build_request(
        prompt="draw a mountain",
        size="1024x1024",
        output_format="png",
        output_compression=None,
        extra_kwargs={},
    )

    assert endpoint == _GENERIC_CALL_ENDPOINT
    assert request_body_type == "json"
    assert payload["model"] == "gpt-image-2"
    assert payload["method"] == _METHOD_GENERATIONS
    assert payload["prompt"] == "draw a mountain"
    assert payload["size"] == "1024x1024"
    assert "format" not in payload
    assert output_format == "png"


def test_build_request_uses_generic_call_multipart_for_edit(tmp_path: Path):
    provider = AntGptImageProvider(
        api_key="test-key",
        base_url="https://matrixcube.alipay.com",
        model_name="gpt-image-2",
        sync_enabled=False,
        async_enabled=False,
    )

    image_path = tmp_path / "cat.png"
    image_path.write_bytes(b"fake-png-bytes")

    endpoint, payload, _, request_body_type = provider._build_request(
        prompt="make the cat yellow",
        size="auto",
        output_format="jpeg",
        output_compression=80,
        extra_kwargs={
            "image_urls": [str(image_path)],
            "quality": "high",
        },
    )

    assert endpoint == _GENERIC_CALL_ENDPOINT
    assert request_body_type == "multipart"
    assert payload["model"] == "gpt-image-2"
    assert payload["method"] == _METHOD_EDITS
    assert payload["prompt"] == "make the cat yellow"
    assert payload["quality"] == "high"
    assert payload["output_compression"] == 80
    assert payload["image"]["filename"] == "cat.png"
    assert payload["image"]["content"] == b"fake-png-bytes"


def test_build_request_includes_mask_for_edit(tmp_path: Path):
    provider = AntGptImageProvider(
        api_key="test-key",
        base_url="https://matrixcube.alipay.com",
        model_name="gpt-image-2",
        sync_enabled=False,
        async_enabled=False,
    )

    image_path = tmp_path / "scene.png"
    mask_path = tmp_path / "mask.png"
    image_path.write_bytes(b"scene-bytes")
    mask_path.write_bytes(b"mask-bytes")

    _, payload, _, request_body_type = provider._build_request(
        prompt="add a flamingo to the pool",
        size="1024x1024",
        output_format="png",
        output_compression=None,
        extra_kwargs={
            "image_urls": [str(image_path)],
            "mask_path": str(mask_path),
        },
    )

    assert request_body_type == "multipart"
    assert payload["method"] == _METHOD_EDITS
    assert payload["mask"]["filename"] == "mask.png"
    assert payload["mask"]["content"] == b"mask-bytes"


def test_generation_payload_strips_image_urls_from_image_agent():
    provider = AntGptImageProvider(
        api_key="test-key",
        base_url="https://matrixcube.alipay.com",
        model_name="gpt-image-2",
        sync_enabled=False,
        async_enabled=False,
    )

    _, payload, _, request_body_type = provider._build_request(
        prompt="a cute cat",
        size="1024x1024",
        output_format="png",
        output_compression=None,
        extra_kwargs={"image_urls": []},
    )

    assert request_body_type == "json"
    assert "image_urls" not in payload
    assert payload["method"] == _METHOD_GENERATIONS


def test_parse_image_response_reads_gateway_body():
    provider = AntGptImageProvider(
        api_key="test-key",
        base_url="https://matrixcube.alipay.com",
        model_name="gpt-image-2",
        sync_enabled=False,
        async_enabled=False,
    )

    import base64

    encoded = base64.b64encode(b"png-bytes").decode("ascii")
    response = provider._parse_image_response(
        {
            "created": 1758077173,
            "background": "opaque",
            "data": [{"b64_json": encoded}],
            "output_format": "png",
            "quality": "high",
            "size": "1024x1536",
            "usage": {"total_tokens": 6285},
            "request_id": "2509171045DIMGFL00013056",
        },
        output_format="png",
    )

    assert response.id == "2509171045DIMGFL00013056"
    assert response.image_bytes == len(b"png-bytes")
    assert response.image_format == "png"
    assert response.usage["gateway_usage"]["total_tokens"] == 6285
