def register_schema(context, state):
    return {
        "fields": {
            "memory_notes": "list[str]",
        }
    }


def bootstrap_context(context, state):
    return {
        "memory_notes": list(state.get("notes", [])),
    }


def enrich_context(context, state):
    workspace = context.get("workspace", {})
    return {
        "workspace_label": workspace.get("name", "unknown"),
    }


def propagate_context(context, state, target):
    return {
        "target_kind": target.get("kind", "unknown"),
    }


def persist_context(context, state):
    return {
        "saved": True,
        "note_count": len(state.get("notes", [])),
    }
