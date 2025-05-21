# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.utils.import_package import import_package

import_package('opentelemetry.trace',
               install_name='opentelemetry-api', version='1.32.1')
import_package('opentelemetry.sdk.trace',
               install_name='opentelemetry-sdk', version='1.32.1')
import_package('opentelemetry.sdk.trace.export',
               install_name='opentelemetry-exporter-otlp', version='1.32.1')
import_package('opentelemetry.instrumentation.system_metrics',
               install_name='opentelemetry-instrumentation-system-metrics', version='0.53b1')
