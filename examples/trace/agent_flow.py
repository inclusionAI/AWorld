from aworld.trace.server import get_trace_server
from aworld.trace.constants import RunType, SPAN_NAME_PREFIX_EVENT_AGENT


def _get_agent_show_name(span: dict):
    agent_name_prefix = SPAN_NAME_PREFIX_EVENT_AGENT
    name = span.get("name")
    if name and name.startswith(agent_name_prefix):
        name = name[len(agent_name_prefix):]
    if name and '---' in name:
        name = name.split('---', 1)[0]
    return name


def _remove_span_detail(root_spans: list):
    keys_to_keep = {'show_name', 'children'}
    for span in root_spans:
        keys_to_remove = [key for key in span.keys() if key not in keys_to_keep]
        for key in keys_to_remove:
            span.pop(key, None)
        if 'children' in span:
            _remove_span_detail(span['children'])


def get_agent_flow(trace_id):
    storage = get_trace_server().get_storage()
    spans = storage.get_all_spans(trace_id)
    spans_dict = {span.span_id: span.dict() for span in spans}
    children_spans = []

    filtered_spans = {}
    for span_id, span in spans_dict.items():
        if span.get('is_event', False) and span.get('run_type') == RunType.AGNET.value:
            span['show_name'] = _get_agent_show_name(span)
            filtered_spans[span_id] = span

    sub_task_spans = []
    for span in list(filtered_spans.values()):
        skip_this_span = False
        parent_id = span['parent_id'] if span['parent_id'] else None

        while parent_id and parent_id not in filtered_spans:
            parent_span = spans_dict.get(parent_id)
            if parent_span and parent_span.get('run_type') == RunType.TASK.value and str(parent_span['attributes'].get('is_sub_task')).lower() == 'true':
                sub_task_spans.append(span)
                skip_this_span = True
                break
            parent_id = parent_span['parent_id'] if parent_span and parent_span['parent_id'] else None

        if skip_this_span:
            continue
        if parent_id:
            parent_span = filtered_spans.get(parent_id)
            if not parent_span:
                continue

            if 'children' not in parent_span:
                parent_span['children'] = []
            parent_span['children'].append(span)
            children_spans.append(span)

    filtered_span_list = [span for span in filtered_spans.values() if span not in sub_task_spans]
    root_spans = [span for span in filtered_span_list
                  if span not in children_spans]
    _remove_span_detail(root_spans)
    return root_spans
