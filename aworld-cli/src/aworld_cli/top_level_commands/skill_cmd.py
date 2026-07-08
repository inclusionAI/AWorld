from __future__ import annotations

import shutil
from pathlib import Path

from aworld_cli.core.installed_skill_manager import InstalledSkillManager
from aworld_cli.core.skill_state_manager import SkillStateManager
from aworld_cli.core.skill_toggle_manager import SkillToggleManager


class SkillTopLevelCommand:
    def _runtime_skill_directory_for_remove(
        self, runtime_skills, skill_name: str
    ) -> Path:
        runtime_skill_configs = runtime_skills.get_all_skills()
        skill_config = runtime_skill_configs.get(skill_name)
        if not skill_config:
            raise ValueError(f"Unknown installed skill entry: {skill_name}")

        skill_path_value = skill_config.get("skill_path")
        if not isinstance(skill_path_value, str) or not skill_path_value:
            raise ValueError(f"Runtime skill has no removable path: {skill_name}")

        skill_path = Path(skill_path_value).expanduser().resolve()
        if skill_path.name != "SKILL.md" or not skill_path.is_file():
            raise ValueError(f"Runtime skill path is not removable: {skill_path}")

        skill_dir = skill_path.parent
        if skill_dir.name != skill_name:
            raise ValueError(
                f"Runtime skill directory does not match skill name: {skill_path}"
            )

        source_roots = [
            Path(source_path).expanduser().resolve()
            for source_path in runtime_skills.source_paths
            if "github.com" not in source_path and not source_path.startswith("git@")
        ]
        if skill_dir.parent not in source_roots:
            raise ValueError(
                f"Runtime skill path is outside removable skill sources: {skill_path}"
            )
        if skill_dir.is_symlink():
            raise ValueError(f"Runtime skill directory is a symlink: {skill_dir}")

        return skill_dir

    def _remove_runtime_skill(self, runtime_skills, skill_name: str) -> Path:
        skill_dir = self._runtime_skill_directory_for_remove(runtime_skills, skill_name)
        shutil.rmtree(skill_dir)
        SkillStateManager().enable_skill(skill_name)
        return skill_dir

    def _print_runtime_skills(self, runtime_skills) -> None:
        runtime_skill_configs = runtime_skills.get_all_skills()
        state_manager = SkillStateManager()
        if runtime_skills.source_paths:
            print("📚 Runtime skill sources:")
            for source_path in runtime_skills.source_paths:
                print(f"  - {source_path}")

        if runtime_skill_configs:
            print("🧠 Runtime skills:")
            for skill_name, skill_config in sorted(runtime_skill_configs.items()):
                skill_path = str(skill_config.get("skill_path", "") or "—")
                print(
                    f"  - {skill_name} | enabled={state_manager.is_enabled(skill_name)} | path={skill_path}"
                )

    @property
    def name(self) -> str:
        return "skill"

    @property
    def description(self) -> str:
        return "Installed skill management commands"

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple()

    def register_parser(self, subparsers) -> None:
        skill_parser = subparsers.add_parser(
            "skill",
            help=self.description,
            description=self.description,
            prog="aworld-cli skill",
        )
        skill_subparsers = skill_parser.add_subparsers(
            dest="skill_action",
            help="Skill action to perform",
            required=True,
        )

        install_parser = skill_subparsers.add_parser(
            "install", help="Install a skill package"
        )
        install_parser.add_argument("source", help="Git URL or local skill directory")
        install_parser.add_argument(
            "--scope",
            default="global",
            help="Install scope: global or agent:<name>",
        )
        install_parser.add_argument(
            "--mode",
            choices=["clone", "copy", "symlink"],
            default=None,
            help="Install mode override",
        )

        skill_subparsers.add_parser("list", help="List installed skill packages")

        enable_parser = skill_subparsers.add_parser(
            "enable", help="Enable an installed skill package by install id, package name, or skill name"
        )
        enable_parser.add_argument(
            "install_id",
            help="Install id, package name, or contained skill name",
        )

        disable_parser = skill_subparsers.add_parser(
            "disable", help="Disable an installed skill package by install id, package name, or skill name"
        )
        disable_parser.add_argument(
            "install_id",
            help="Install id, package name, or contained skill name",
        )

        remove_parser = skill_subparsers.add_parser(
            "remove", help="Remove an installed skill package"
        )
        remove_parser.add_argument("install_id", help="Install id or name")

        update_parser = skill_subparsers.add_parser(
            "update", help="Update a git-backed skill package"
        )
        update_parser.add_argument("install_id", help="Install id or name")

        import_parser = skill_subparsers.add_parser(
            "import", help="Import a manually placed installed skill entry"
        )
        import_parser.add_argument("path", help="Path under ~/.aworld/skills/installed")
        import_parser.add_argument(
            "--scope",
            default="global",
            help="Install scope: global or agent:<name>",
        )

    def run(self, args, context) -> int | None:
        manager = InstalledSkillManager()
        toggle_manager = SkillToggleManager(installed_manager=manager)
        from aworld_cli.core.runtime_skill_registry import build_runtime_skill_registry_view

        try:
            if args.skill_action == "install":
                source_path = Path(args.source).expanduser()
                mode = args.mode or ("copy" if source_path.exists() else "clone")
                record = manager.install(
                    source=args.source,
                    mode=mode,
                    scope=args.scope,
                )
                print(f"✅ Skill package '{record['install_id']}' installed successfully")
                print(f"📍 Location: {record['installed_path']}")
                return 0

            if args.skill_action == "list":
                installs = sorted(
                    manager.list_installs(), key=lambda item: str(item["install_id"])
                )
                runtime_skills = build_runtime_skill_registry_view()
                if not installs:
                    print("📦 No installed skill packages")
                    print(f"📍 Installed root: {manager.installed_root}")
                    self._print_runtime_skills(runtime_skills)
                    return 0
                for install in installs:
                    print(
                        f"{install['install_id']} | enabled={install.get('enabled', True)} | scope={install['scope']} | "
                        f"skill_count={install['skill_count']} | source={install['source']}"
                    )
                self._print_runtime_skills(runtime_skills)
                return 0

            if args.skill_action == "enable":
                result = toggle_manager.enable(args.install_id)
                if result.target_kind == "package":
                    print(f"✅ Skill package '{result.identifier}' enabled successfully")
                else:
                    print(f"✅ Skill '{result.identifier}' enabled successfully")
                return 0

            if args.skill_action == "disable":
                result = toggle_manager.disable(args.install_id)
                if result.target_kind == "package":
                    print(f"✅ Skill package '{result.identifier}' disabled successfully")
                else:
                    print(f"✅ Skill '{result.identifier}' disabled successfully")
                return 0

            if args.skill_action == "remove":
                try:
                    manager.remove_install(args.install_id)
                except ValueError as exc:
                    if not str(exc).startswith("Unknown installed skill entry:"):
                        raise
                    runtime_skills = build_runtime_skill_registry_view()
                    removed_path = self._remove_runtime_skill(
                        runtime_skills, args.install_id
                    )
                    print(
                        f"✅ Runtime skill '{args.install_id}' removed successfully"
                    )
                    print(f"📍 Removed: {removed_path}")
                    return 0
                print(f"✅ Skill package '{args.install_id}' removed successfully")
                return 0

            if args.skill_action == "update":
                record = manager.update_install(args.install_id)
                print(f"✅ Skill package '{record['install_id']}' updated successfully")
                return 0

            if args.skill_action == "import":
                record = manager.import_entry(Path(args.path), scope=args.scope)
                print(f"✅ Skill package '{record['install_id']}' imported successfully")
                return 0

            print(f"❌ Unsupported skill action: {args.skill_action}")
            return 1
        except Exception as exc:
            print(f"❌ {exc}")
            return 1
