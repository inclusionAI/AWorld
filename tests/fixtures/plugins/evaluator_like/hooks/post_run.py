import json
from pathlib import Path


def handle_event(event, state):
    output_path = Path(event["workspace_path"]) / "hook-output.json"
    output_path.write_text(json.dumps(event["report"]), encoding="utf-8")
    return {"action": "allow"}
