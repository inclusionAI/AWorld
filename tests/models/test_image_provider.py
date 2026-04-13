import pytest
from pathlib import Path

from aworld.models.image_provider import ImageProvider


def test_build_request_uses_json_generation_for_text_to_image():
    provider = ImageProvider(
        api_key="test-key",
        base_url="https://antchat.alipay.com",
        model_name="Qwen-Image-2512-Lightning",
        sync_enabled=False,
        async_enabled=False,
    )

    endpoint, payload, output_format, request_body_type = provider._build_request(
        prompt="draw a mountain",
        negative_prompt=None,
        size="1024x1024",
        response_format="b64_json",
        output_format="png",
        output_compression=None,
        seed=None,
        user=None,
        extra_kwargs={},
    )

    assert endpoint == "/v1/images/generations"
    assert request_body_type == "json"
    assert payload["model"] == "Qwen-Image-2512-Lightning"
    assert payload["prompt"] == "draw a mountain"
    assert payload["size"] == "1024x1024"
    assert output_format == "png"


def test_build_request_uses_multipart_edits_with_remote_url():
    provider = ImageProvider(
        api_key="test-key",
        base_url="https://antchat.alipay.com",
        model_name="Qwen-Image-Edit-2511-Lightning",
        sync_enabled=False,
        async_enabled=False,
    )

    endpoint, payload, _, request_body_type = provider._build_request(
        prompt="把背景改成海边日落",
        negative_prompt="模糊",
        size="1328x1328",
        response_format="b64_json",
        output_format="png",
        output_compression=None,
        seed=7,
        user="tester",
        extra_kwargs={
            "image_urls": ["https://example.com/input.png"],
            "watermark": False,
            "prompt_extend": True,
            "guidance_scale": 4.5,
        },
    )

    assert endpoint == "/v1/images/edits"
    assert request_body_type == "multipart"
    assert payload["model"] == "Qwen-Image-Edit-2511-Lightning"
    assert payload["url[]"] == ["https://example.com/input.png"]
    assert payload["prompt"] == "把背景改成海边日落"
    assert payload["size"] == "1328x1328"
    assert payload["response_format"] == "b64_json"
    assert payload["output_format"] == "png"
    assert payload["negative_prompt"] == "模糊"
    assert payload["seed"] == 7
    assert payload["user"] == "tester"
    assert payload["watermark"] is False
    assert payload["prompt_extend"] is True
    assert payload["guidance_scale"] == 4.5
    assert "image[]" not in payload


def test_edit_model_without_input_images_falls_back_to_generations():
    provider = ImageProvider(
        api_key="test-key",
        base_url="https://antchat.alipay.com",
        model_name="Qwen-Image-Edit-2511-Lightning",
        sync_enabled=False,
        async_enabled=False,
    )

    endpoint, payload, _, request_body_type = provider._build_request(
        prompt="A cute fluffy cat with bright eyes",
        negative_prompt=None,
        size="1024x1024",
        response_format="b64_json",
        output_format="png",
        output_compression=None,
        seed=None,
        user=None,
        extra_kwargs={},
    )

    assert endpoint == "/v1/images/generations"
    assert request_body_type == "json"
    assert payload["model"] == "Qwen-Image-Edit-2511-Lightning"
    assert payload["prompt"] == "A cute fluffy cat with bright eyes"


def test_edit_request_uses_image_field_for_data_url_inputs():
    provider = ImageProvider(
        api_key="test-key",
        base_url="https://antchat.alipay.com",
        model_name="Qwen-Image-Edit-2511-Lightning",
        sync_enabled=False,
        async_enabled=False,
    )

    endpoint, payload, _, request_body_type = provider._build_request(
        prompt="把猫咪改成戴红围巾",
        negative_prompt=None,
        size="1024x1024",
        response_format="b64_json",
        output_format="png",
        output_compression=None,
        seed=None,
        user=None,
        extra_kwargs={
            "image_urls": ["data:image/png;base64,abc123"],
        },
    )

    assert endpoint == "/v1/images/edits"
    assert request_body_type == "multipart"
    assert payload["url[]"] == ["data:image/png;base64,abc123"]
    assert "image[]" not in payload


def test_edit_request_uses_image_file_field_for_local_file_inputs(tmp_path: Path):
    provider = ImageProvider(
        api_key="test-key",
        base_url="https://antchat.alipay.com",
        model_name="Qwen-Image-Edit-2511-Lightning",
        sync_enabled=False,
        async_enabled=False,
    )

    image_path = tmp_path / "cat.png"
    image_path.write_bytes(b"fake-png-bytes")

    endpoint, payload, _, request_body_type = provider._build_request(
        prompt="把猫咪改成戴红围巾",
        negative_prompt=None,
        size="1024x1024",
        response_format="b64_json",
        output_format="png",
        output_compression=None,
        seed=None,
        user=None,
        extra_kwargs={
            "image_urls": [str(image_path)],
        },
    )

    assert endpoint == "/v1/images/edits"
    assert request_body_type == "multipart"
    assert "url[]" not in payload
    assert len(payload["image[]"]) == 1
    assert payload["image[]"][0]["filename"] == "cat.png"
    assert payload["image[]"][0]["content"] == b"fake-png-bytes"
    assert payload["image[]"][0]["content_type"] == "image/png"


def test_edit_request_supports_mixed_local_file_and_data_url_inputs(tmp_path: Path):
    provider = ImageProvider(
        api_key="test-key",
        base_url="https://antchat.alipay.com",
        model_name="Qwen-Image-Edit-2511-Lightning",
        sync_enabled=False,
        async_enabled=False,
    )

    image_path = tmp_path / "cat.webp"
    image_path.write_bytes(b"fake-webp-bytes")

    endpoint, payload, _, request_body_type = provider._build_request(
        prompt="把两张图融合成一张",
        negative_prompt=None,
        size="1024x1024",
        response_format="url",
        output_format="png",
        output_compression=None,
        seed=None,
        user=None,
        extra_kwargs={
            "reference_images": [str(image_path), "data:image/png;base64,abc123"],
        },
    )

    assert endpoint == "/v1/images/edits"
    assert request_body_type == "multipart"
    assert len(payload["image[]"]) == 1
    assert payload["image[]"][0]["filename"] == "cat.webp"
    assert payload["url[]"] == ["data:image/png;base64,abc123"]
