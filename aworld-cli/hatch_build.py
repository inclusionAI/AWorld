"""Bundle the canonical repository skills into wheels and standalone sdists."""

from __future__ import annotations

import json
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        root = Path(self.root)
        packaged_root = root / "src" / "aworld_cli" / "builtin_skills"
        manifest = json.loads((packaged_root / "manifest.json").read_text(encoding="utf-8"))
        destination_root = (
            "src/aworld_cli/builtin_skills"
            if self.target_name == "sdist"
            else "aworld_cli/builtin_skills"
        )
        for name, entry in manifest["skills"].items():
            source = root.parent / entry["source"]
            if not (source / "SKILL.md").is_file():
                # A published sdist already contains the same resources in
                # the package tree. Hatch includes them without another mapping.
                source = packaged_root / name
                if (source / "SKILL.md").is_file():
                    continue
            if not (source / "SKILL.md").is_file():
                raise FileNotFoundError(f"Missing built-in skill source: {entry['source']}")
            build_data["force_include"][str(source)] = f"{destination_root}/{name}"
