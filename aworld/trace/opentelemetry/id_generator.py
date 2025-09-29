import os
import time
import secrets
from opentelemetry import trace as sdk_trace
from opentelemetry.sdk.trace.id_generator import IdGenerator


class ProcessSafeIdGenerator(IdGenerator):

    def __init__(self):
        self.pid = os.getpid()
        self.start_time = int(time.time() * 1000)
        self.counter = 0

    def generate_span_id(self) -> int:
        '''
        Generate a span id.
        The span id is a 64-bit integer, which is composed of:
        - lower 32 bits are random
        - upper 16 bits are pid
        - middle 16 bits are counter
        '''
        span_id = 0
        while span_id == sdk_trace.INVALID_SPAN_ID:
            # lower 32 bits are random
            rand_part = secrets.randbits(32)
            # upper 16 bits are pid
            pid_part = (self.pid & 0xFFFF) << 16
            # middle 16 bits are counter
            counter_part = (self.counter & 0xFFFF)

            span_id = (pid_part | counter_part) << 32 | rand_part
            # increment counter
            self.counter = (self.counter + 1) & 0xFFFF
            # wait for next millisecond if counter overflow
            if self.counter == 0:
                time.sleep(0.001)

        print(f"generate_span_id: {span_id}")
        return span_id

    def generate_trace_id(self) -> int:
        '''
        Generate a trace id.
        The trace id is a 128-bit integer, which is composed of:
        - higher 64 bits are pid and timestamp
        - lower 64 bits are random
        '''
        trace_id = 0
        while trace_id == sdk_trace.INVALID_TRACE_ID:
            # higher 64 bits are pid and timestamp
            pid_part = self.pid & 0xFFFFFFFF
            time_part = int(time.time() * 1000) & 0xFFFFFFFF
            high_part = (pid_part << 32) | time_part

            # lower 64 bits are random
            low_part = secrets.randbits(64)

            trace_id = (high_part << 64) | low_part
            # increment counter
            self.counter = (self.counter + 1) & 0xFFFF
            # wait for next millisecond if counter overflow
            if self.counter == 0:
                time.sleep(0.001)

        print(f"generate_trace_id: {trace_id}")
        return trace_id
