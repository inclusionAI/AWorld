from re import L
from aworld.trace.base import Propagator, Carrier, TraceContext
from aworld.trace.baggage import BaggageContext
from aworld.logs.util import logger


class SofaTracerBaggagePropagator(Propagator):
    """
    Sofa tracer baggage propagator.
    """

    _TRACE_ID_HEDER_NAMES = ["SOFA-TraceId", "sofaTraceId"]
    _SPAN_ID_HEDER_NAMES = ["SOFA-RpcId", "sofaRpcId"]
    _PEN_ATTRS_HEDER_NAME = "sofaPenAttrs"
    _SYS_PEN_ATTRS_HEDER_NAME = "sysPenAttrs"

    _TRACE_ID_BAGGAGE_KEY = "attributes.sofa.traceid"
    _SPAN_ID_BAGGAGE_KEY = "attributes.sofa.rpcid"
    _PEN_ATTRS_BAGGAGE_KEY = "attributes.sofa.penattrs"
    _SYS_PEN_ATTRS_BAGGAGE_KEY = "attributes.sofa.syspenattrs"

    def _get_value(self, carrier: Carrier, name: str) -> str:
        """
        Get value from carrier.
        Args:
            carrier: The carrier to get value from.
            name: The name of the value.
        Returns:
            The value of the name.
        """
        return carrier.get(name) or carrier.get('HTTP_' + name.upper().replace('-', '_'))

    def extract(self, carrier: Carrier):
        """
        Extract trace context from carrier.
        Args:
            carrier: The carrier to extract trace context from.
        Returns:
            A dict of trace context.
        """
        trace_id = None
        span_id = None
        for name in self._TRACE_ID_HEDER_NAMES:
            trace_id = self._get_value(carrier, name)
            if trace_id:
                break
        for name in self._SPAN_ID_HEDER_NAMES:
            span_id = self._get_value(carrier, name)
            if span_id:
                break
        pen_attrs = self._get_value(carrier, self._PEN_ATTRS_HEDER_NAME)
        sys_pen_attrs = self._get_value(
            carrier, self._SYS_PEN_ATTRS_HEDER_NAME)

        logger.info(
            f"extract trace_id: {trace_id}, span_id: {span_id}, pen_attrs: {pen_attrs}, sys_pen_attrs: {sys_pen_attrs}")
        if trace_id and span_id:
            BaggageContext.set_baggage(self._TRACE_ID_BAGGAGE_KEY, trace_id)
            span_id = span_id + ".1"
            BaggageContext.set_baggage(self._SPAN_ID_BAGGAGE_KEY, span_id)
            if pen_attrs:
                BaggageContext.set_baggage(
                    self._PEN_ATTRS_BAGGAGE_KEY, pen_attrs)
            if sys_pen_attrs:
                BaggageContext.set_baggage(
                    self._SYS_PEN_ATTRS_BAGGAGE_KEY, sys_pen_attrs)

    def inject(self, trace_context: TraceContext, carrier: Carrier):
        """
        Inject trace context to carrier.
        Args:
            trace_context: The trace context to inject.
            carrier: The carrier to inject trace context to.
        """
        trace_id = BaggageContext.get_baggage_value(self._TRACE_ID_BAGGAGE_KEY)
        span_id = BaggageContext.get_baggage_value(self._SPAN_ID_BAGGAGE_KEY)
        pen_attrs = BaggageContext.get_baggage_value(
            self._PEN_ATTRS_BAGGAGE_KEY)
        sys_pen_attrs = BaggageContext.get_baggage_value(
            self._SYS_PEN_ATTRS_BAGGAGE_KEY)
        if trace_id and span_id:
            carrier.set(self._TRACE_ID_HEDER_NAMES[0], trace_id)
            carrier.set(self._SPAN_ID_HEDER_NAMES[0], span_id)
            if pen_attrs:
                carrier.set(self._PEN_ATTRS_HEDER_NAME, pen_attrs)
            if sys_pen_attrs:
                carrier.set(self._SYS_PEN_ATTRS_HEDER_NAME, sys_pen_attrs)
