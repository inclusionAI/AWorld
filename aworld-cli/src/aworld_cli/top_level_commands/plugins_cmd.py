from __future__ import annotations

from pathlib import Path

class PluginsTopLevelCommand:
    @property
    def name(self) -> str:
        return "plugins"

    @property
    def description(self) -> str:
        return "Plugin management commands"

    @property
    def aliases(self) -> tuple[str, ...]:
        return ("plugin",)

    def register_parser(self, subparsers) -> None:
        plugin_parser = subparsers.add_parser(
            "plugins",
            help=self.description,
            description=self.description,
            prog="aworld-cli plugins",
        )
        plugin_subparsers = plugin_parser.add_subparsers(
            dest="plugin_action",
            help="Plugin action to perform",
        )

        install_parser = plugin_subparsers.add_parser("install", help="Install a plugin")
        install_parser.add_argument("plugin_name", help="Name of the plugin to install")
        install_parser.add_argument("--url", type=str, help="Plugin repository URL (GitHub or other git URL)")
        install_parser.add_argument("--local-path", type=str, help="Local plugin path")
        install_parser.add_argument("--force", action="store_true", help="Force reinstall/overwrite existing plugin")

        remove_parser = plugin_subparsers.add_parser("remove", help="Remove a plugin")
        remove_parser.add_argument("plugin_name", help="Name of the plugin to remove")

        enable_parser = plugin_subparsers.add_parser("enable", help="Enable an installed plugin")
        enable_parser.add_argument("plugin_name", help="Name of the plugin to enable")

        disable_parser = plugin_subparsers.add_parser("disable", help="Disable an installed plugin")
        disable_parser.add_argument("plugin_name", help="Name of the plugin to disable")

        reload_parser = plugin_subparsers.add_parser("reload", help="Reload plugin metadata from disk")
        reload_parser.add_argument("plugin_name", help="Name of the plugin to reload")

        validate_parser = plugin_subparsers.add_parser(
            "validate",
            help="Validate a plugin manifest or plugin root",
        )
        validate_parser.add_argument("plugin_name", nargs="?", help="Installed plugin name to validate")
        validate_parser.add_argument("--path", type=str, help="Explicit plugin root path to validate")

        plugin_subparsers.add_parser("list", help="List installed plugins")

    def run(self, args, context) -> int | None:
        from aworld_cli.core import plugin_manager as plugin_manager_module

        plugin_action = getattr(args, "plugin_action", None) or "list"
        manager = plugin_manager_module.PluginManager()

        if plugin_action == "install":
            if not args.url and not args.local_path:
                print("❌ Error: Either --url or --local-path must be provided")
                return 1
            try:
                success = manager.install(
                    plugin_name=args.plugin_name,
                    url=args.url,
                    local_path=args.local_path,
                    force=args.force,
                )
                if success:
                    print(f"✅ Plugin '{args.plugin_name}' installed successfully")
                    print(f"📍 Location: {manager.plugin_dir / args.plugin_name}")
                    return 0
                print(f"❌ Failed to install plugin '{args.plugin_name}'")
                return 1
            except Exception as exc:
                print(f"❌ Error installing plugin: {exc}")
                return 1

        if plugin_action == "remove":
            success = manager.remove(args.plugin_name)
            return 0 if success else 1

        if plugin_action == "enable":
            try:
                plugin_state = manager.enable(args.plugin_name)
                print(f"✅ Plugin '{args.plugin_name}' enabled")
                print(f"📍 Location: {plugin_state['path']}")
                return 0
            except KeyError:
                print(f"❌ Plugin '{args.plugin_name}' is not installed")
                return 1

        if plugin_action == "disable":
            try:
                plugin_state = manager.disable(args.plugin_name)
                print(f"✅ Plugin '{args.plugin_name}' disabled")
                print(f"📍 Location: {plugin_state['path']}")
                return 0
            except KeyError:
                print(f"❌ Plugin '{args.plugin_name}' is not installed")
                return 1

        if plugin_action == "reload":
            try:
                plugin_state = manager.reload(args.plugin_name)
                print(f"✅ Plugin '{args.plugin_name}' reloaded")
                print(f"📍 Location: {plugin_state['path']}")
                return 0
            except KeyError:
                print(f"❌ Plugin '{args.plugin_name}' is not installed")
                return 1

        if plugin_action == "validate":
            try:
                if args.path:
                    plugin_state = plugin_manager_module.validate_plugin_path(Path(args.path))
                    label = plugin_state["plugin_id"]
                elif args.plugin_name:
                    plugin_state = manager.validate(args.plugin_name)
                    label = args.plugin_name
                else:
                    print("❌ Error: specify either <plugin_name> or --path")
                    return 1

                print(f"✅ Plugin '{label}' is valid")
                print(f"📍 Location: {plugin_state['path']}")
                print(f"🆔 Plugin ID: {plugin_state['plugin_id']}")
                print(f"🧩 Framework: {plugin_state['framework_source']}")
                print(f"⚙️ Capabilities: {', '.join(plugin_state['capabilities']) or '-'}")
                return 0
            except KeyError:
                print(f"❌ Plugin '{args.plugin_name}' is not installed")
                return 1
            except Exception as exc:
                print(f"❌ Plugin validation failed: {exc}")
                return 1

        if plugin_action == "list":
            print(
                plugin_manager_module.render_plugins_table(
                    plugin_manager_module.list_available_plugins(manager),
                    manager.plugin_dir,
                ),
                end="",
            )
            return 0

        print(f"❌ Unsupported plugin action: {plugin_action}")
        return 1
