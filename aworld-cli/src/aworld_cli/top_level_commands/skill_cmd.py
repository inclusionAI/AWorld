from __future__ import annotations

from pathlib import Path

from aworld_cli.core.installed_skill_manager import InstalledSkillManager


class SkillTopLevelCommand:
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
            "enable", help="Enable an installed skill package"
        )
        enable_parser.add_argument("install_id", help="Install id or name")

        disable_parser = skill_subparsers.add_parser(
            "disable", help="Disable an installed skill package"
        )
        disable_parser.add_argument("install_id", help="Install id or name")

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
            if not installs:
                print("📦 No installed skill packages")
                print(f"📍 Installed root: {manager.installed_root}")
                return 0
            for install in installs:
                print(
                    f"{install['install_id']} | enabled={install.get('enabled', True)} | scope={install['scope']} | "
                    f"skill_count={install['skill_count']} | source={install['source']}"
                )
            return 0

        if args.skill_action == "enable":
            record = manager.enable_install(args.install_id)
            print(f"✅ Skill package '{record['install_id']}' enabled successfully")
            return 0

        if args.skill_action == "disable":
            record = manager.disable_install(args.install_id)
            print(f"✅ Skill package '{record['install_id']}' disabled successfully")
            return 0

        if args.skill_action == "remove":
            manager.remove_install(args.install_id)
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

        raise ValueError(f"Unsupported skill action: {args.skill_action}")
