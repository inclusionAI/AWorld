"""Runtime finalization of task-owned publication journals.

Run only after the controlling agent process has exited. The caller supplies a
private per-run root, never a model-selected path. Summaries are diagnostics and
must not be treated as external benchmark verdicts.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

from .store import TaskWorkspaceStore
from .store_io import atomic_json, read_json


def recover_workspace_root(root, *, max_scopes=1024):
    root = Path(root)
    if root.is_symlink() or root != root.resolve():
        raise ValueError("recovery root must be an absolute non-symlink directory")
    results = []
    if root.exists():
        entries = list(root.iterdir())
        if len(entries) > max_scopes:
            raise ValueError("too many workspace scopes for one finalization")
        for path in sorted(entries):
            if (
                path.is_symlink()
                or not path.is_dir()
                or not re.fullmatch(r"[0-9a-f]{64}", path.name)
            ):
                raise ValueError("unexpected entry in scoped workspace root")
            try:
                store = TaskWorkspaceStore.open_existing(path)
                recovery = deepcopy(store.last_recovery_result)
                followup = store.recover()
                if followup.get("recovered"):
                    recovery = followup
                delivery_path = path / "delivery-session.json"
                state = read_json(delivery_path) if delivery_path.exists() else {}
                latest = state.get("last_final_validation") or {}
                validation = latest.get("receipt") or latest
                delivery = {
                    "contract": state.get("delivery"),
                    "self_check_ids": [c["id"] for c in state.get("self_checks", [])],
                    "self_check_revision_count": len(
                        state.get("self_check_history", [])
                    ),
                    "protection_errors": state.get("protection_errors", []),
                    "last_validation": {
                        "success": latest.get("success"),
                        "checks": [
                            {
                                key: c.get(key)
                                for key in (
                                    "id",
                                    "kind",
                                    "path",
                                    "status",
                                    "success",
                                    "error_type",
                                    "definition_sha256",
                                )
                            }
                            for c in validation.get("checks", [])
                        ],
                        "metrics": validation.get("metrics", {}),
                        "error": validation.get("error"),
                    },
                }
                results.append(
                    {
                        "scope_id": path.name,
                        "success": True,
                        "recovery": recovery,
                        "state": store.status(),
                        "readback": store.readback(),
                        "provenance": store.provenance(),
                        "delivery": delivery,
                    }
                )
            except (OSError, ValueError, RuntimeError) as exc:
                results.append(
                    {
                        "scope_id": path.name,
                        "success": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
    return {
        "schema_version": "aworld.workspace-recovery/v1",
        "success": all(r["success"] for r in results),
        "scopes": results,
        "task_reward": "not_assessed",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = recover_workspace_root(args.root)
    encoded = json.dumps(result, ensure_ascii=False).encode()
    if len(encoded) > 8 * 1024 * 1024:
        raise ValueError("recovery summary exceeds operation output allowance")
    atomic_json(args.output, result)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
