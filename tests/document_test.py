# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import os
import unittest
from pathlib import Path

from aworld.config.conf import ToolConfig

from examples.tools.common import Tools
from examples.tools.document.document import DocumentTool


class TestDocumentTool(unittest.TestCase):
    def setUp(self):
        self.document_tool = DocumentTool(name=Tools.DOCUMENT_ANALYSIS.value, conf=ToolConfig())

    def tearDown(self):
        self.document_tool.close()

    def test_document_analysis(self):
        current_dir = Path(__file__).parent.absolute()
        document_path = os.path.join(current_dir, 'test.json')
        content, keyframes, error = self.document_tool.document_analysis(document_path)
        self.assertEqual(content, {'test': 'test content'})
        self.assertEqual(keyframes, [])
        self.assertEqual(error, None)
