import logging
import asyncio
from fastapi import APIRouter
from aworld.trace.server import get_trace_server
from aworld.trace.constants import RunType
from aworld.trace.server.util import build_trace_tree
from aworld.cmd.utils.trace_summarize import summarize_trace

logger = logging.getLogger(__name__)

router = APIRouter()

prefix = "/api/trace"


@router.get("/list")
async def list_traces():
    storage = get_trace_server().get_storage()
    trace_data = []
    for trace_id in storage.get_all_traces():
        spans = storage.get_all_spans(trace_id)
        spans_sorted = sorted(spans, key=lambda x: x.start_time)
        trace_tree = build_trace_tree(spans_sorted)
        trace_data.append({
            'trace_id': trace_id,
            'root_span': trace_tree,
        })
    return {
        "data": trace_data
    }


@router.get("/agent")
async def get_agent_trace(trace_id: str):
    task = asyncio.create_task(summarize_trace(trace_id))
    storage = get_trace_server().get_storage()
    spans = storage.get_all_spans(trace_id)
    spans_dict = {span.span_id: span.dict() for span in spans}

    filtered_spans = {}
    for span_id, span in spans_dict.items():
        if span.get('is_event', False) and span.get('run_type') == RunType.AGNET.value:
            span['show_name'] = _get_agent_show_name(span)
            filtered_spans[span_id] = span

    for span in list(filtered_spans.values()):
        parent_id = span['parent_id'] if span['parent_id'] else None

        while parent_id and parent_id not in filtered_spans:
            parent_span = spans_dict.get(parent_id)
            parent_id = parent_span['parent_id'] if parent_span and parent_span['parent_id'] else None

        if parent_id:
            parent_span = filtered_spans.get(parent_id)
            if not parent_span:
                continue

            if 'children' not in parent_span:
                parent_span['children'] = []
            parent_span['children'].append(span)

    root_spans = [span for span in filtered_spans.values()
                  if span['parent_id'] is None or span['parent_id'] not in filtered_spans]
    await task
    return {
        "data": root_spans
    }


def _get_agent_show_name(span: dict):
    agent_name_prefix = "agent_event_"
    name = span.get("name")
    if name and name.startswith(agent_name_prefix):
        name = name[len(agent_name_prefix):]
    return name
