"""Stdlib ATIF export from canonical Context history, independent of execution."""

import json
import os
from pathlib import Path
import tempfile

from aworld._version import __version__
from aworld.core.agent.usage import summarize_usage


def write_json(path, value, *, default=None):
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".aworld-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, default=default, allow_nan=False)
            stream.write("\n")
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def build_trajectory(history, *, result, agent, model_name=None):
    steps, calls = [], {}
    history = tuple(history)
    receipts, run_receipts = [], []

    def metrics(usage):
        if not isinstance(usage, dict):
            return {}
        return {"metrics": {"prompt_tokens": usage.get("input_tokens"),
                            "completion_tokens": usage.get("output_tokens"),
                            "cached_tokens": usage.get("cache_read_tokens"), "extra": usage}}

    for entry in history:
        if entry.kind in ("assistant", "model.error", "context.summary"):
            receipts.append(entry.data.get("usage"))
            if entry.run_id == result.run_id:
                run_receipts.append(entry.data.get("usage"))
        if entry.kind == "system":
            steps.append({"step_id": len(steps) + 1, "source": "system", "message": entry.data["content"],
                          "extra": {"run_id": entry.run_id, **{key: value for key, value in entry.data.items() if key != "content"}}})
        elif entry.kind == "input":
            steps.append({"step_id": len(steps) + 1, "source": "user", "message": entry.data,
                          "extra": {"run_id": entry.run_id}})
        elif entry.kind == "assistant":
            step = {"step_id": len(steps) + 1, "source": "agent", "message": entry.data["content"],
                    "llm_call_count": 1, "extra": {"run_id": entry.run_id}, **metrics(entry.data.get("usage"))}
            if entry.data["tool_calls"]:
                step["tool_calls"] = [{"tool_call_id": call["id"], "function_name": call["name"],
                                      "arguments": call["arguments"]} for call in entry.data["tool_calls"]]
                for call in step["tool_calls"]:
                    calls[(entry.run_id, call["tool_call_id"])] = step
            steps.append(step)
        elif entry.kind == "model.error":
            steps.append({"step_id": len(steps) + 1, "source": "agent", "llm_call_count": 1,
                          "message": "Model call failed", "extra": {"run_id": entry.run_id,
                          "error_type": entry.data["error_type"]}, **metrics(entry.data.get("usage"))})
        elif entry.kind == "context.summary":
            steps.append({"step_id": len(steps) + 1, "source": "agent", "llm_call_count": 1,
                          "message": entry.data["content"], "extra": {"run_id": entry.run_id,
                          **{key: value for key, value in entry.data.items() if key not in ("content", "usage")}},
                          **metrics(entry.data.get("usage"))})
        elif entry.kind in ("context.compaction", "context.compaction.failed"):
            steps.append({"step_id": len(steps) + 1, "source": "system", "message": entry.kind,
                          "extra": {"run_id": entry.run_id, **entry.data}})
        elif entry.kind == "tool.result":
            data = entry.data
            step = calls[(entry.run_id, data["tool_call_id"])]
            observation = step.setdefault("observation", {"results": []})
            observation["results"].append({"source_call_id": data["tool_call_id"],
                "content": json.dumps(data["content"], ensure_ascii=False, allow_nan=False),
                "extra": {"is_error": data["is_error"]}})
    session_usage, run_usage = summarize_usage(receipts), summarize_usage(run_receipts)
    context_budget = next((entry.data["context"] for entry in reversed(history)
                           if entry.kind == "system" and entry.run_id == result.run_id and "context" in entry.data), None)
    return {"schema_version": "ATIF-v1.7", "session_id": result.session_id,
            "trajectory_id": result.run_id,
            "agent": {"name": "aworld", "version": __version__, "model_name": model_name,
                      "extra": {"skills": [{"name": skill.name, "location": skill.location} for skill in agent.skills],
                                "tools": [tool.name for tool in agent.tools]}},
            "steps": steps,
            "final_metrics": {"total_prompt_tokens": session_usage["input_tokens"],
                              "total_completion_tokens": session_usage["output_tokens"],
                              "total_cached_tokens": session_usage["cache_read_tokens"],
                              "total_steps": len(steps), "extra": {"scope": "session", **session_usage}},
            "extra": {"run_id": result.run_id, "status": result.status.value,
                                      "stop_reason": result.stop_reason.value,
                                      "history_scope": "session", "token_usage": run_usage["status"],
                                      "context_budget": context_budget,
                                      "compaction": {"completed": sum(entry.kind == "context.compaction" and entry.run_id == result.run_id for entry in history),
                                                     "failed": sum(entry.kind == "context.compaction.failed" and entry.run_id == result.run_id for entry in history)},
                                      "run_metrics": {"scope": "run", "usage_scope": "main_agent_and_compaction",
                                                      "run_id": result.run_id, **run_usage}}}
