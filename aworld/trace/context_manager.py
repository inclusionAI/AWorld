import importlib.metadata as importlib_metadata
import types
import inspect
from typing import TYPE_CHECKING, Union, Optional, Any, Type, Sequence, Callable
from aworld.trace.trace import AttributeValueType, NoOpSpan, Span, Tracer, NoOpTracer, get_tracer_provider, log_trace_error
from aworld.trace.auto_trace import AutoTraceModule, install_auto_tracing
from aworld.trace.stack_info import get_user_stack_info
from aworld.trace.constants import (
    ATTRIBUTES_MESSAGE_KEY,
    ATTRIBUTES_MESSAGE_TEMPLATE_KEY
)
from aworld.trace.msg_format import (
    chunks_formatter,
    warn_formatting,
    FStringAwaitError,
    KnownFormattingError,
    warn_fstring_await
)

class TraceManager:
    """
    TraceManager is a class that provides a way to trace the execution of a function.
    """
    def __init__(self, tracer_name: str = None) -> None:
        self._tracer_name = tracer_name or "aworld"
        self._version = importlib_metadata.version('aworld')

    def _create_auto_span(self,
                          name: str,
                          attributes: dict[str, AttributeValueType] = None
    ) -> Span:
        """
        Create a auto trace span with the given name and attributes.
        """
        try:
            tracer = get_tracer_provider().get_tracer(name=self._tracer_name, version=self._version)
            return ContextSpan(span_name=name, tracer=tracer, attributes=attributes)
        except Exception:
            return ContextSpan(span_name=name, tracer=NoOpTracer(), attributes=attributes)

    def new_manager(self, tracer_name_suffix: str = None) -> "TraceManager":
        """
        Create a new TraceManager with the given tracer name suffix.
        """
        tracer_name = self._tracer_name if not tracer_name_suffix else f"{self._tracer_name}.{tracer_name_suffix}"
        return TraceManager(tracer_name=tracer_name)

    def auto_tracing(self,
                     modules: Union[Sequence[str], Callable[[AutoTraceModule], bool]],
                     min_duration: float
    ) -> None:
        """
        Automatically trace the execution of a function.
        """
        install_auto_tracing(self, modules, min_duration)

    def span(self,
             msg_template: str,
             attributes: dict[str, AttributeValueType] = None,
             *,
             span_name: str = None
    ) -> "ContextSpan":

        try:
            attributes = attributes or {}
            stack_info = get_user_stack_info()
            merged_attributes = {**stack_info, **attributes}
            # Retrieve stack information of user code and add it to the attributes
            
            fstring_frame = inspect.currentframe().f_back  # type: ignore
            log_message, extra_attrs, msg_template = format_span_msg(
                msg_template,
                merged_attributes,
                fstring_frame=fstring_frame,
            )
            merged_attributes[ATTRIBUTES_MESSAGE_KEY] = log_message
            merged_attributes.update(extra_attrs)
            merged_attributes[ATTRIBUTES_MESSAGE_TEMPLATE_KEY] = msg_template

            span_name = span_name or msg_template
            tracer = get_tracer_provider().get_tracer(name=self._tracer_name, version=self._version)
            return ContextSpan(span_name=span_name,
                               tracer=tracer, 
                               attributes=merged_attributes)
        except Exception:
            log_trace_error()
            return ContextSpan(span_name=span_name, tracer=NoOpTracer(), attributes=attributes)



class ContextSpan(NoOpSpan):
    """A context manager that wraps an existing `Span` object.
    This class provides a way to use a `Span` object as a context manager.
    When the context manager is entered, it returns the `Span` itself.
    When the context manager is exited, it calls `end` on the `Span`.
    Args:
        span: The `Span` object to wrap.
    """

    def __init__(self,
                 span_name: str,
                 tracer: Tracer,
                 attributes: dict[str, AttributeValueType] = None) -> None:
        self._span_name = span_name
        self._tracer = tracer
        self._attributes = attributes
        self._span: Span = None

    if not TYPE_CHECKING:  # pragma: no branch
        def __getattr__(self, name: str) -> Any:
            return getattr(self._span, name)

    def _start(self):
        if self._span is not None:
            return

        self._span = self._tracer.start_span(
            name=self._span_name,
            attributes=self._attributes,
        )

    def __enter__(self) -> "Span":
        self._start()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        traceback: Optional[Any],
    ) -> None:
        """Ends context manager and calls `end` on the `Span`."""
        if self._span and self._span.is_recording() and isinstance(exc_val, BaseException):
            self._span.record_exception(exc_val, escaped=True)
        self._span.end()

    

def format_span_msg(
    format_string: str,
    kwargs: dict[str, Any],
    fstring_frame: types.FrameType = None,
) -> tuple[str, dict[str, Any], str]:
    # Returns
    # 1. The formatted message.
    # 2. A dictionary of extra attributes to add to the span/log.
    #      These can come from evaluating values in f-strings.
    # 3. The final message template, which may differ from `format_string` if it was an f-string.
    try:
        chunks, extra_attrs, new_template = chunks_formatter.chunks(
            format_string,
            kwargs,
            fstring_frame=fstring_frame
        )
        return ''.join(chunk['v'] for chunk in chunks), extra_attrs, new_template
    except KnownFormattingError as e:
        warn_formatting(str(e) or str(e.__cause__))
    except FStringAwaitError as e:
        warn_fstring_await(str(e))
    except Exception:
        log_trace_error()

    # Formatting failed, so just use the original format string as the message.
    return format_string, {}, format_string

