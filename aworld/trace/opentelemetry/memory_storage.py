import os
import json
import time
import threading
from datetime import datetime
from abc import ABC, abstractmethod
from collections import defaultdict
from pydantic import BaseModel
from typing import Optional, Dict, Any, Union
from opentelemetry.sdk.trace import Span, SpanContext
from opentelemetry.sdk.trace.export import SpanExporter
from aworld.logs.util import logger
from aworld.replay_buffer.processor import ReplayBufferExporter

class SpanStatus(BaseModel):
    code: str = "UNSET"
    description: Optional[str] = None

class SpanModel(BaseModel):
    trace_id: str
    span_id: str
    name: str
    start_time: str
    end_time: str
    duration_ms: float
    attributes: Dict[str, Any]
    status: SpanStatus
    parent_id: Optional[str]
    children: list['SpanModel'] = []

    @staticmethod
    def from_span(span):
        start_timestamp = span.start_time / 1e9
        end_timestamp = span.end_time / 1e9
        start_ms = int((span.start_time % 1e9) / 1e6)
        end_ms = int((span.end_time % 1e9) / 1e6)

        return SpanModel(
            trace_id=f"{span.get_span_context().trace_id:032x}",
            span_id=SpanModel.get_span_id(span),
            name=span.name,
            start_time=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(start_timestamp)) + f'.{start_ms:03d}',
            end_time=time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(end_timestamp)) + f'.{end_ms:03d}',
            duration_ms=(span.end_time - span.start_time)/1e6,
            attributes={k: v for k, v in span.attributes.items()},
            status=SpanStatus(
                code=str(span.status.status_code) if span.status.status_code else "UNSET",
                description=span.status.description or None
            ),
            parent_id=SpanModel.get_span_id(span.parent) if span.parent else None
        )

    @staticmethod
    def get_span_id(span: Union[Span, SpanContext]):
        if isinstance(span, SpanContext):
            return f"{span.span_id:016x}"
        return f"{span.get_span_context().span_id:016x}"


class TraceStorage(ABC):
    """
    Storage for traces.
    """
    @abstractmethod
    def add_span(self, span: Span) -> None:
        """
        Add a span to the storage.
        """

    @abstractmethod
    def get_all_traces(self) -> list[str]:
        """
        Get all trace ids.
        """

    @abstractmethod
    def get_all_spans(self, trace_id) -> list[SpanModel]:
        """
        Get all spans of a trace.
        """


class InMemoryStorage(TraceStorage):
    """
    In-memory storage for spans.
    """
    def __init__(self):
        self._traces = defaultdict(list)
        # {trace_id: [span1, span2, ...]}

    def add_span(self, span: Span):
        trace_id = f"{span.get_span_context().trace_id:032x}"
        self._traces[trace_id].append(SpanModel.from_span(span))

    def get_all_traces(self):
        return list(self._traces.keys())

    def get_all_spans(self, trace_id):
        return self._traces.get(trace_id, [])

class InMemoryWithPersistStorage(TraceStorage):
    """
    In-memory storage for spans with optimized disk persistence.
    """
    def __init__(self, storage_dir: str = "./trace_data"):
        self._traces = defaultdict(list)
        self._pending_spans = []
        self.storage_dir = os.path.abspath(storage_dir)
        os.makedirs(self.storage_dir, exist_ok=True)
        self._lock = threading.Lock()
        self._persist_thread = None
        self._load_today_traces()
        
    def _get_today_filename(self):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"trace_{timestamp}.json"
        
    def _load_today_traces(self):
        today = datetime.now().strftime("%Y%m%d")
        for filename in os.listdir(self.storage_dir):
            if filename.startswith(f"trace_{today}") and filename.endswith(".json"):
                filepath = os.path.join(self.storage_dir, filename)
                try:
                    with self._lock, open(filepath, 'r') as f:
                        data = json.load(f)
                        for span_data in data:
                            trace_id = span_data.get("trace_id")
                            span_json = span_data.get("span")
                            self._traces[trace_id].append(SpanModel.parse_raw(span_json))
                except Exception as e:
                    logger.error(f"Error loading trace file {filename}: {str(e)}")
    
    def _start_persist_thread(self):
        if self._persist_thread is None:
            self._persist_thread = threading.Thread(target=self._persist_worker, daemon=True)
            self._persist_thread.start()
    
    def _persist_worker(self):
        while True:
            time.sleep(5)
            self._persist()
    
    def _persist(self):
        if not self._pending_spans:
            return
            
        temp_filepath = os.path.join(self.storage_dir, f"temp_{time.time_ns()}.json")
        final_filepath = os.path.join(self.storage_dir, self._get_today_filename())
        
        try:
            spans_to_persist = []
            with self._lock:
                spans_to_persist = self._pending_spans.copy()
                self._pending_spans.clear()
            
            if spans_to_persist:
                existing_data = []
                if os.path.exists(final_filepath):
                    try:
                        with open(final_filepath, 'r') as f:
                            existing_data = json.load(f)
                    except Exception as e:
                        logger.error(f"Error reading existing trace file: {str(e)}")
                
                merged_spans = existing_data + spans_to_persist
                
                with open(temp_filepath, 'w') as f:
                    json.dump(merged_spans, f, default=str)
                os.replace(temp_filepath, final_filepath)
        except Exception as e:
            logger.error(f"Error persisting traces: {str(e)}")
            try:
                os.unlink(temp_filepath)
            except:
                pass

    def add_span(self, span: Span):
        span_model = SpanModel.from_span(span)
        with self._lock:
            self._traces[span_model.trace_id].append(span_model)
            self._pending_spans.append({
                "trace_id": span_model.trace_id,
                "span": span_model.json()
            })
        self._start_persist_thread()
    
    def get_all_traces(self):
        with self._lock:
            return list(self._traces.keys())

    def get_all_spans(self, trace_id):
        with self._lock:
            return self._traces.get(trace_id, [])



class InMemorySpanExporter(SpanExporter):
    """
    Span exporter that stores spans in memory.
    """
    def __init__(self, storage: TraceStorage):
        self._storage = storage

    def export(self, spans):
        span_model_list = []
        for span in spans:
            self._storage.add_span(span)
            span_model_list.append(SpanModel.from_span(span).model_dump())

        if (os.getenv("EXPORT_REPLAY_TRACE_TO_FILES") or "true").lower() == "true":
            storage_dir = self._storage.storage_dir if hasattr(self._storage, "storage_dir") else "./trace_data"
            replay_dir = os.path.join(storage_dir, "replays")
            replay_dataset_path = os.getenv("REPLAY_TRACE_DATASET_PATH", replay_dir)
            output_dir = os.path.abspath(replay_dataset_path)
            ReplayBufferExporter.replay_buffer_exporter(spans=span_model_list, output_dir=output_dir)

    def shutdown(self):
        pass
