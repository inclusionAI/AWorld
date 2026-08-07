import asyncio
import sys
from pathlib import Path


def _add_src_path() -> None:
    src_path = Path(__file__).resolve().parent.parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))


def test_file_backed_rate_limiter_spaces_calls_across_instances(tmp_path: Path) -> None:
    _add_src_path()
    from document_parse_service.media_transcription.file_rate_limiter import (
        FileBackedRateLimiter,
    )

    now = [100.0]
    delays: list[float] = []

    async def _advance_clock(delay: float) -> None:
        delays.append(delay)
        now[0] += delay

    first = FileBackedRateLimiter(
        state_dir=tmp_path,
        clock=lambda: now[0],
        sleep=_advance_clock,
    )
    second = FileBackedRateLimiter(
        state_dir=tmp_path,
        clock=lambda: now[0],
        sleep=_advance_clock,
    )

    first_wait_ms = asyncio.run(first.acquire(key="provider-model-key", requests_per_minute=60))
    second_wait_ms = asyncio.run(second.acquire(key="provider-model-key", requests_per_minute=60))

    assert first_wait_ms == 0
    assert second_wait_ms == 1000
    assert delays == [1.0]
