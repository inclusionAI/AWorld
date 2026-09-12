from pathlib import Path
import tomllib

from packaging.requirements import Requirement


ROOT = Path(__file__).resolve().parents[3]
LOCKED_PILLOW_VERSION = "12.1.1"
LOCKED_MCP_VERSION = "1.29.1"


def _requirement_named(lines: list[str], name: str) -> Requirement:
    for line in lines:
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        requirement = Requirement(candidate)
        if requirement.name.lower() == name.lower():
            return requirement
    raise AssertionError(f"missing {name!r} requirement")


def test_aworld_and_filex_accept_the_same_locked_pillow_version() -> None:
    aworld_requirements = (ROOT / "aworld" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    filex_project = tomllib.loads(
        (ROOT / "aworld-tools" / "filex" / "pyproject.toml").read_text(
            encoding="utf-8"
        )
    )["project"]

    aworld_pillow = _requirement_named(aworld_requirements.splitlines(), "pillow")
    filex_pillow = _requirement_named(filex_project["dependencies"], "pillow")

    assert LOCKED_PILLOW_VERSION in aworld_pillow.specifier
    assert LOCKED_PILLOW_VERSION in filex_pillow.specifier


def test_aworld_accepts_the_mcp_version_locked_with_filex_fastmcp() -> None:
    aworld_requirements = (ROOT / "aworld" / "requirements.txt").read_text(
        encoding="utf-8"
    )
    filex_lock = tomllib.loads(
        (ROOT / "aworld-tools" / "filex" / "uv.lock").read_text(encoding="utf-8")
    )

    aworld_mcp = _requirement_named(aworld_requirements.splitlines(), "mcp")
    locked_versions = {
        package["name"]: package["version"] for package in filex_lock["package"]
    }

    assert locked_versions["fastmcp"] == "2.11.3"
    assert locked_versions["mcp"] == LOCKED_MCP_VERSION
    assert LOCKED_MCP_VERSION in aworld_mcp.specifier
