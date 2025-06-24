import logging
from fastapi import APIRouter
from aworld.trace.server import get_trace_server
from aworld.trace.constants import RunType

logger = logging.getLogger(__name__)

router = APIRouter()

prefix = "/api/trace"


@router.get("/agent")
async def get_agent_trace(trace_id: str):
    storage = get_trace_server().get_storage()
    spans = storage.get_all_spans(trace_id)
    spans_dict = {span.span_id: span.dict() for span in spans}

    filtered_spans = {}
    for span_id, span in spans_dict.items():
        if span.get('is_event', False) and span.get('run_type') == RunType.AGNET.value:
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
    return {
        "data": root_spans
    }
