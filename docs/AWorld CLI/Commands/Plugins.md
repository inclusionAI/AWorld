# Plugins

## What It Does

The `/plugins` slash command manages framework plugins inside the current interactive session. It is the fastest way to inspect installed plugins and toggle them on or off without leaving the session.

## Commands

```text
/plugins
/plugins list
/plugins enable <plugin_name>
/plugins disable <plugin_name>
/plugins reload <plugin_name>
/plugins validate <plugin_name>
```

## Typical Workflow

1. Run `/plugins list` to inspect what is installed.
2. Enable or disable a plugin by name.
3. Use `/plugins reload <plugin_name>` after changing plugin files on disk.
4. Use `/plugins validate <plugin_name>` to verify the manifest and entrypoint layout.

## Related Pages

- See [Plugin SDK](../Plugins/Plugin%20SDK.md) for the supported plugin manifest and entrypoint contract.
- See [Ralph Session Loop](../Plugins/Ralph%20Session%20Loop.md) for a built-in plugin example.

## Notes And Limits

- Slash-command validation works on installed plugin names. If you need to validate an arbitrary plugin root on disk, use the top-level CLI command: `aworld-cli plugins validate --path /path/to/plugin`.
- Enabling, disabling, or reloading a plugin also refreshes the current session plugin state when the runtime supports it.
