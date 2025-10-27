# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.core.context.amni.processor import BaseContextProcessor
from aworld.runners.handler import DefaultHandler
from aworld.utils.common import scan_packages

scan_packages("aworld.core.context", [BaseContextProcessor, DefaultHandler])

