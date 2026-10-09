"""Stdlib ATIF export from canonical Context history, independent of execution."""

import json
import os
from pathlib import Path
import tempfile

from aworld import __version__
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


def build_trajectory(history, *, result, agent, model_name=None, events=()):
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
            if "replayed_reasoning_chars" in entry.data:
                step["extra"].update(reasoning_chars=len(entry.data.get("reasoning_content") or ""),
                                     replayed_reasoning_chars=entry.data["replayed_reasoning_chars"])
            if entry.data["tool_calls"]:
                step["tool_calls"] = [{"tool_call_id": call["id"], "function_name": call["name"],
                                      "arguments": call["arguments"]} for call in entry.data["tool_calls"]]
                for call in step["tool_calls"]:
                    calls[(entry.run_id, call["tool_call_id"])] = step
            steps.append(step)
        elif entry.kind == "model.error":
            steps.append({"step_id": len(steps) + 1, "source": "agent", "llm_call_count": 1,
                          "message": "Model call failed", "extra": {"run_id": entry.run_id,
                          "error_type": entry.data["error_type"],
                          **{key: entry.data[key] for key in ("error_code", "diagnostics", "will_retry") if key in entry.data}},
                          **metrics(entry.data.get("usage"))})
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
    events = tuple(events)
    timings, run_durations = _call_timings(events)
    if timings:
        for run_id in _attach_timings(steps, timings):
            receipts.append(None)
            if run_id == result.run_id:
                run_receipts.append(None)
    session_usage, run_usage = summarize_usage(receipts), summarize_usage(run_receipts)
    if run_durations:
        session_usage.update(_timing_summary(timings, run_durations))
        run_usage.update(_timing_summary([r for r in timings if r["run_id"] == result.run_id],
            {key: value for key, value in run_durations.items() if key == result.run_id}))
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


def _call_timings(events):
    """Measure executor events on the kernel's monotonic Run clock."""
    active, records, run_durations = {}, [], {}
    for event in events:
        now = event.elapsed_ms
        if now is None:
            continue
        data = event.data if isinstance(event.data, dict) else {}
        if event.type == "run.finished":
            run_durations[event.run_id] = now
            reason = getattr(event.data.stop_reason, "value", event.data.stop_reason)
            for key in list(active):
                if key[0] == event.run_id:
                    record = active.pop(key)
                    interrupted = reason in ("cancelled", "deadline_exceeded")
                    record.update(duration_ms=round(max(0, now - record['started_elapsed_ms']), 3),
                                  finished_elapsed_ms=now, interrupted=interrupted,
                                  status="interrupted" if interrupted else "failed")
                    records.append(record)
            continue
        if event.type.startswith("context.summary."):
            kind, action, purpose, ident = "model", event.type.rsplit(".", 1)[1], "compaction", data.get("attempt")
        elif event.type.startswith(("model.", "tool.")):
            kind, action = event.type.split(".", 1)
            purpose = "main_agent"
            ident = data.get("turn") if kind == "model" else data.get("id", data.get("tool_call_id"))
        else:
            continue
        if ident is None or action not in ("started", "finished", "failed"):
            continue
        key = (event.run_id, kind, purpose, str(ident))
        if action == "started":
            active[key] = {"run_id": event.run_id, "kind": kind, "purpose": purpose,
                           "id": str(ident), "name": data.get("name", ""),
                           "started_elapsed_ms": now, "seq": event.seq}
        elif key in active:
            record = active.pop(key)
            record.update(duration_ms=round(max(0, now - record['started_elapsed_ms']), 3),
                          finished_elapsed_ms=now, interrupted=False,
                          status="failed" if action == "failed" or data.get("is_error") else "completed")
            records.append(record)
    return sorted(records, key=lambda row: (row['run_id'], row['seq'])), run_durations


def _timing_summary(records, run_durations):
    tools = {}
    for record in records:
        if record['kind'] != 'tool':
            continue
        name = record['name']
        row = tools.setdefault(name, dict(tool_name=name, call_count=0, completed_count=0,
                                         failed_count=0, interrupted_count=0, total_duration_ms=0,
                                         max_duration_ms=0))
        row['call_count'] += 1
        row[record['status'] + '_count'] += 1
        row['total_duration_ms'] += record['duration_ms']
        row['max_duration_ms'] = max(row['max_duration_ms'], record['duration_ms'])
    for row in tools.values():
        row['total_duration_ms'] = round(row['total_duration_ms'], 3)
        row['mean_duration_ms'] = round(row['total_duration_ms'] / row['call_count'], 3)
    return {"timing_source": "aworld.run_events", "timing_scope": "main_agent_and_compaction",
            "total_model_duration_ms": round(sum(r['duration_ms'] for r in records if r['kind'] == 'model'), 3),
            "total_model_wall_duration_ms": round(sum(r['duration_ms'] for r in records if r['kind'] == 'model'), 3),
            "total_tool_call_duration_ms": round(sum(r['duration_ms'] for r in records if r['kind'] == 'tool'), 3),
            "total_run_duration_ms": round(sum(run_durations.values()), 3),
            "llm_request_count": sum(r['kind'] == 'model' for r in records),
            "tool_call_count": sum(r['kind'] == 'tool' for r in records),
            "interrupted_call_count": sum(r['interrupted'] for r in records),
            "tool_metrics": [tools[name] for name in sorted(tools)]}


def _attach_timings(steps, records):
    model_steps, tool_steps, offsets, missing_receipts = {}, {}, {}, []
    for step in steps:
        run_id = step.get('extra', {}).get('run_id')
        if step['source'] == 'agent':
            purpose = 'compaction' if step.get('extra', {}).get('purpose') == 'compaction' else 'main_agent'
            model_steps.setdefault((run_id, purpose), []).append(step)
        for call in step.get('tool_calls', []):
            tool_steps[(run_id, call['tool_call_id'])] = (step, call)
    for record in records:
        timing = {"timing_source": "aworld.run_events", "duration_ms": record['duration_ms'],
                  "started_elapsed_ms": record['started_elapsed_ms'],
                  "finished_elapsed_ms": record['finished_elapsed_ms'],
                  "status": record['status'], "interrupted": record['interrupted']}
        if record['kind'] == 'model':
            key = record['run_id'], record['purpose']
            index = offsets.get(key, 0)
            group = model_steps.get(key, [])
            if index < len(group):
                step = group[index]
            else:
                # A cancelled model call has no assistant response or receipt.
                step = {"source": "agent", "message": "Model request " + record['status'],
                        "llm_call_count": 1, "extra": {"run_id": record['run_id'],
                        "purpose": record['purpose'], "diagnostic": True}}
                end = max((i for i, s in enumerate(steps) if s.get('extra', {}).get('run_id') == record['run_id']), default=len(steps)-1)
                steps.insert(end + 1, step)
                missing_receipts.append(record['run_id'])
            offsets[key] = index + 1
            extra = step.setdefault('metrics', {}).setdefault('extra', {})
            extra.update({**timing, 'model_duration_ms': record['duration_ms'],
                          'model_wall_duration_ms': record['duration_ms']})
        else:
            pair = tool_steps.get((record['run_id'], record['id']))
            if pair is None:
                continue
            step, call = pair
            call.setdefault('extra', {}).update(timing)
            for observation in step.get('observation', {}).get('results', []):
                if observation.get('source_call_id') == record['id']:
                    observation.setdefault('extra', {}).update(timing)
    for index, step in enumerate(steps, 1):
        step['step_id'] = index
        if step['source'] != 'agent':
            continue
        tool_times = [call.get('extra', {}).get('duration_ms') for call in step.get('tool_calls', [])]
        if not tool_times or any(value is not None for value in tool_times):
            step.setdefault('metrics', {}).setdefault('extra', {})['tool_call_duration_ms'] = round(sum(v for v in tool_times if v is not None), 3)
    return missing_receipts
