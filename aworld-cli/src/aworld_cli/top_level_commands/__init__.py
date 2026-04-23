from __future__ import annotations

def register_builtin_top_level_commands(registry) -> None:
    # Keep the builtin registry hook so kernel-owned top-level commands can be
    # added later. The `skill` command is now contributed through the framework
    # plugin bootstrap path instead of hardcoded registration here.
    return None
