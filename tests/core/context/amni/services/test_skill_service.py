from __future__ import annotations

from types import SimpleNamespace

import pytest

from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.services.skill_service import (
    ACTIVE_SKILLS_KEY,
    SKILL_LIST_KEY,
    SkillService,
)


class _FakeContext:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], object] = {}
        self.swarm = SimpleNamespace(agents={})

    def put(self, key: str, value: object, namespace: str | None = None) -> None:
        self._store[(namespace or "default", key)] = value

    def get(self, key: str, namespace: str | None = None) -> object:
        return self._store.get((namespace or "default", key))


@pytest.mark.asyncio
async def test_skill_service_keeps_inactive_skill_usage_out_of_compat_list() -> None:
    context = _FakeContext()
    service = SkillService(context)

    await service.init_skill_list(
        {
            "browser-use": {
                "name": "browser-use",
                "description": "Browser automation",
                "usage": "Use browser tools",
                "tool_list": {"browser": {}},
                "skill_path": "/tmp/browser-use/SKILL.md",
                "active": False,
            },
            "code-review": {
                "name": "code-review",
                "description": "Review code",
                "usage": "Review code carefully",
                "tool_list": {"shell": {}},
                "skill_path": "/tmp/code-review/SKILL.md",
                "active": True,
            },
        },
        namespace="agent-1",
    )

    all_skills = await service.get_skill_list("agent-1")
    active_skills = await service.get_active_skills("agent-1")

    assert active_skills == ["code-review"]
    assert all_skills["browser-use"].get("usage", "") == ""
    assert all_skills["browser-use"].get("tool_list", {}) == {}
    assert all_skills["code-review"]["usage"] == "Review code carefully"


@pytest.mark.asyncio
async def test_skill_service_loads_inactive_skill_content_on_first_get() -> None:
    context = _FakeContext()
    service = SkillService(context)

    await service.init_skill_list(
        {
            "browser-use": {
                "name": "browser-use",
                "description": "Browser automation",
                "usage": "Use browser tools",
                "tool_list": {"browser": {}},
                "skill_path": "/tmp/browser-use/SKILL.md",
                "active": False,
            }
        },
        namespace="agent-1",
    )

    before = await service.get_skill_list("agent-1")
    skill = await service.get_skill("browser-use", "agent-1")
    after = await service.get_skill_list("agent-1")

    assert before["browser-use"].get("usage", "") == ""
    assert skill["usage"] == "Use browser tools"
    assert after["browser-use"]["usage"] == "Use browser tools"


@pytest.mark.asyncio
async def test_application_context_init_skill_list_delegates_to_skill_service() -> None:
    calls: list[tuple[dict[str, object], str]] = []

    class _FakeSkillService:
        async def init_skill_list(self, skill_list, namespace):
            calls.append((skill_list, namespace))

    context = ApplicationContext.__new__(ApplicationContext)
    context._skill_service = _FakeSkillService()
    context.put = lambda *args, **kwargs: None

    await ApplicationContext.init_skill_list(
        context,
        {"browser-use": {"name": "browser-use"}},
        namespace="agent-1",
    )

    assert calls == [({"browser-use": {"name": "browser-use"}}, "agent-1")]
