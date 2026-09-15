from pathlib import Path

import pytest

from aworld.utils.runtime_state import get_runtime_state_root, runtime_state_path


def test_runtime_state_path_preserves_default_without_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWORLD_CONTROL_ROOT", raising=False)

    assert get_runtime_state_root() is None
    assert runtime_state_path("cron.json", default=".aworld/cron.json") == Path(
        ".aworld/cron.json"
    )


def test_runtime_state_path_uses_control_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    control_root = tmp_path / "control"
    monkeypatch.setenv("AWORLD_CONTROL_ROOT", str(control_root))

    assert get_runtime_state_root() == control_root.resolve()
    assert runtime_state_path(
        "sessions", "transcripts", default=".aworld/sessions/transcripts"
    ) == control_root / "sessions" / "transcripts"


def test_runtime_state_path_rejects_absolute_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AWORLD_CONTROL_ROOT", str(tmp_path / "control"))

    with pytest.raises(ValueError, match="must be relative"):
        runtime_state_path(tmp_path / "escape", default="unused")


def test_cron_scheduler_store_uses_control_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from aworld.core.scheduler import get_scheduler, reset_scheduler

    control_root = tmp_path / "control"
    monkeypatch.setenv("AWORLD_CONTROL_ROOT", str(control_root))
    reset_scheduler()

    scheduler = get_scheduler()

    assert scheduler.store.file_path == control_root / "cron.json"
    reset_scheduler()
