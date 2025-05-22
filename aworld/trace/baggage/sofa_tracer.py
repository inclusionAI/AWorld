from aworld.trace.base import Propagator, Carrier, TraceContext
from aworld.baggage import BaggageContext


class SofaTracerBaggagePropagator(Propagator):

    _TRACE_ID_HEDER_NAMES = ["SOFA-TraceId", "sofaTraceId"],
    _SPAN_ID_HEDER_NAMES = ["SOFA-RpcId", "sofaRpcId"]

    def extract(self, carrier: Carrier) -> TraceContext:
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
            trace_id = carrier.get(name)
            if trace_id:
                break
        for name in self._SPAN_ID_HEDER_NAMES:
            span_id = carrier.get(name)
            if span_id:
                break

        if trace_id and span_id:
            BaggageContext.set_baggage("attributes.sofa.traceid", trace_id)
            span_id = span_id + ".1"
            BaggageContext.set_baggage("attributes.sofa.rpcid", span_id)
