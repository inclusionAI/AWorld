import pytest

import aworld.models.ant_video_provider as ant_video_provider_module
from aworld.core.video_gen_provider import VideoGenerationRequest
from aworld.models.ant_video_provider import AntVideoProvider, ModelAdapter
from aworld.models.model_response import ModelResponse, VideoGenerationResult


class _DummyAdapter(ModelAdapter):
    def build_submit_payload(self, request, model, extra):
        return False, {"prompt": request.prompt}

    def build_status_payload(self, task_id, model, is_image2video):
        return {"task_id": task_id}

    def parse_response(self, data, model, is_image2video=False):
        raw_status = data.get("task_status", "unknown")
        status = {
            "submitted": "submitted",
            "processing": "processing",
            "succeed": "succeeded",
            "failed": "failed",
            "PENDING": "submitted",
        }.get(raw_status, raw_status)
        return ModelResponse(
            id=data.get("task_id", "task-1"),
            model=model,
            video_result=VideoGenerationResult(
                task_id=data.get("task_id", "task-1"),
                status=status,
                video_url=data.get("video_url"),
            ),
            raw_response=data,
        )

    def check_submit_response(self, body, model):
        return None

    def extract_submit_data(self, body):
        return body

    def extract_task_id(self, data):
        return data.get("task_id", "")

    def check_status_response(self, body, model):
        return None

    def extract_status_data(self, body):
        return body

    def get_status_from_data(self, data):
        return data.get("task_status", "unknown")


class _SequentialSyncClient:
    def __init__(self, responses):
        self._responses = list(responses)

    def sync_call(self, payload, endpoint=None):
        if not self._responses:
            raise AssertionError("No more stub responses available")
        return self._responses.pop(0)


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture
def fake_clock(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(ant_video_provider_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(ant_video_provider_module.time, "sleep", clock.sleep)
    return clock


def test_poll_until_done_times_out_when_task_stays_submitted(fake_clock):
    provider = AntVideoProvider(
        model_name="dummy-video-model",
        sync_enabled=False,
        async_enabled=False,
    )
    provider.provider = _SequentialSyncClient(
        [{"task_id": "task-1", "task_status": "submitted"} for _ in range(4)]
    )

    with pytest.raises(TimeoutError, match="queued status 'submitted'"):
        provider._poll_until_done(
            adapter=_DummyAdapter(),
            task_id="task-1",
            model="dummy-video-model",
            is_image2video=False,
            poll_interval=5.0,
            poll_timeout=600.0,
            submitted_timeout=15.0,
        )

    assert fake_clock.now == 15.0


def test_generate_video_uses_default_submitted_timeout(monkeypatch, fake_clock):
    provider = AntVideoProvider(
        model_name="dummy-video-model",
        sync_enabled=False,
        async_enabled=False,
    )
    provider.provider = _SequentialSyncClient(
        [
            {"task_id": "task-1", "task_status": "submitted"},
            {"task_id": "task-1", "task_status": "submitted"},
            {"task_id": "task-1", "task_status": "submitted"},
            {"task_id": "task-1", "task_status": "submitted"},
        ]
    )
    monkeypatch.setattr(ant_video_provider_module, "_resolve_adapter", lambda model: _DummyAdapter())

    request = VideoGenerationRequest(
        prompt="generate a video",
        extra_params={
            "poll": True,
            "poll_interval": 60.0,
            "poll_timeout": 600.0,
        },
    )

    with pytest.raises(TimeoutError, match="120.0s without starting processing"):
        provider.generate_video(request)

    assert fake_clock.now == 120.0


def test_generate_video_falls_back_when_submitted_timeout_is_invalid(monkeypatch, fake_clock):
    provider = AntVideoProvider(
        model_name="dummy-video-model",
        sync_enabled=False,
        async_enabled=False,
    )
    provider.provider = _SequentialSyncClient(
        [
            {"task_id": "task-1", "task_status": "submitted"},
            {"task_id": "task-1", "task_status": "submitted"},
            {"task_id": "task-1", "task_status": "submitted"},
            {"task_id": "task-1", "task_status": "submitted"},
        ]
    )
    monkeypatch.setattr(ant_video_provider_module, "_resolve_adapter", lambda model: _DummyAdapter())

    request = VideoGenerationRequest(
        prompt="generate a video",
        extra_params={
            "poll": True,
            "poll_interval": 60.0,
            "poll_timeout": 600.0,
            "submitted_timeout": "not-a-number",
        },
    )

    with pytest.raises(TimeoutError, match="120.0s without starting processing"):
        provider.generate_video(request)

    assert fake_clock.now == 120.0


def test_poll_until_done_counts_cumulative_time_across_queued_status_variants(fake_clock):
    provider = AntVideoProvider(
        model_name="dummy-video-model",
        sync_enabled=False,
        async_enabled=False,
    )
    provider.provider = _SequentialSyncClient(
        [
            {"task_id": "task-1", "task_status": "submitted"},
            {"task_id": "task-1", "task_status": "pending"},
            {"task_id": "task-1", "task_status": "queued"},
            {"task_id": "task-1", "task_status": "queued"},
        ]
    )

    with pytest.raises(TimeoutError, match="queued status 'queued'"):
        provider._poll_until_done(
            adapter=_DummyAdapter(),
            task_id="task-1",
            model="dummy-video-model",
            is_image2video=False,
            poll_interval=5.0,
            poll_timeout=600.0,
            submitted_timeout=15.0,
        )

    assert fake_clock.now == 15.0


def test_poll_until_done_allows_progress_after_submitted(fake_clock):
    provider = AntVideoProvider(
        model_name="dummy-video-model",
        sync_enabled=False,
        async_enabled=False,
    )
    provider.provider = _SequentialSyncClient(
        [
            {"task_id": "task-1", "task_status": "submitted"},
            {"task_id": "task-1", "task_status": "processing"},
            {"task_id": "task-1", "task_status": "processing"},
            {"task_id": "task-1", "task_status": "succeed", "video_url": "https://example.com/video.mp4"},
        ]
    )

    response = provider._poll_until_done(
        adapter=_DummyAdapter(),
        task_id="task-1",
        model="dummy-video-model",
        is_image2video=False,
        poll_interval=10.0,
        poll_timeout=60.0,
        submitted_timeout=15.0,
    )

    assert response.video_result is not None
    assert response.video_result.status == "succeeded"
    assert response.video_result.video_url == "https://example.com/video.mp4"
    assert fake_clock.now == 30.0
