# # coding: utf-8
# # Copyright (c) 2025 inclusionAI.
import os
import unittest
from pathlib import Path

from aworld.virtual_environments.browsers.browser import BrowserTool

from aworld.config.common import Tools
from aworld.virtual_environments.tool_action import BrowserAction
from aworld.core.common import ActionModel
from aworld.virtual_environments.conf import BrowserToolConfig


class TestBrowserTool(unittest.TestCase):
    def setUp(self):
        self.browser_tool = BrowserTool(BrowserToolConfig(width=1280,
                                                          height=720,
                                                          headless=True,
                                                          keep_browser_open=True), name="browser")
        self.browser_tool.reset()

    def tearDown(self):
        self.browser_tool.close()

    def test_new_tab(self):
        current_dir = Path(__file__).parent.absolute()
        url = "file://" + os.path.join(current_dir, 'test.json')
        action = [ActionModel(tool_name=Tools.BROWSER.value,
                              action_name=BrowserAction.NEW_TAB.value.name,
                              params={"url": url})]
        ob, _, _, _, info = self.browser_tool.step(action)
        self.assertEqual(info, {'exception': ''})

    def test_goto_url(self):
        current_dir = Path(__file__).parent.absolute()
        url = "file://" + os.path.join(current_dir, 'test.json')
        action = [ActionModel(tool_name=Tools.BROWSER.value,
                              action_name=BrowserAction.GO_TO_URL.value.name,
                              params={"url": url})]
        ob, _, _, _, info = self.browser_tool.step(action)
        self.assertEqual(info, {'exception': ''})
        import time
        time.sleep(10)
