"""Explicit skills: inline instructions or a discoverable SKILL.md location."""

from dataclasses import dataclass
from pathlib import Path

from aworld.core.tool.function import Tool


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str = ""
    location: str | None = None
    tools: tuple[Tool, ...] = ()

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Skill name must be non-empty")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("Skill description must be non-empty")
        if not isinstance(self.instructions, str):
            raise TypeError("Skill instructions must be text")
        if self.location is not None:
            object.__setattr__(self, "location", str(Path(self.location).expanduser().resolve()))
        owned = tuple(self.tools)
        if not all(isinstance(tool, Tool) for tool in owned):
            raise TypeError("Skill tools must contain Tool values")
        object.__setattr__(self, "tools", owned)

    @classmethod
    def from_file(cls, path: str | Path) -> "Skill":
        """Load metadata only; the model reads the body with the ordinary read tool.

        YAML parsing is optional and imported only for this explicit file loader.
        This method never installs dependencies, syncs assets, or executes scripts.
        """
        try:
            import yaml
        except ImportError as exc:
            raise ImportError("Skill discovery requires the optional aworld[skills] dependency; use --no-skills to disable it") from exc

        location = Path(path).expanduser().resolve()
        text = location.read_text(encoding="utf-8")
        lines = text.splitlines()
        if not lines or lines[0] != "---":
            raise ValueError(f"Missing SKILL.md frontmatter: {location}")
        try:
            end = lines.index("---", 1)
        except ValueError:
            raise ValueError("Unclosed SKILL.md frontmatter") from None
        metadata = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(metadata, dict):
            raise ValueError("Skill frontmatter must be a mapping")
        return cls(metadata.get("name"), metadata.get("description"), location=str(location))


def load_skills(*directories: str | Path) -> tuple[Skill, ...]:
    """Discover one directory level under explicit roots, without global scanning."""
    skills = []
    for directory in directories:
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            raise NotADirectoryError(str(root))
        files = [root / "SKILL.md"] if (root / "SKILL.md").is_file() else sorted(root.glob("*/SKILL.md"))
        skills.extend(Skill.from_file(path) for path in files)
    names = [skill.name for skill in skills]
    if len(set(names)) != len(names):
        raise ValueError("Skill names must be unique")
    return tuple(skills)
