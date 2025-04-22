# # coding: utf-8
# # Copyright (c) 2025 inclusionAI.
import unittest

from aworld.virtual_environments.browsers.browser import BrowserTool

from aworld.config.common import Tools
from aworld.config.tool_action import BrowserAction
from aworld.core.common import ActionModel
from aworld.virtual_environments.conf import BrowserToolConfig


class TestBrowserTool(unittest.TestCase):
    def setUp(self):
        self.browser_tool = BrowserTool(BrowserToolConfig(width=1280,
                                                          height=720,
                                                          headless=False,
                                                          keep_browser_open=True), name="browser")
        self.browser_tool.reset()

    def tearDown(self):
        self.browser_tool.close()

    def test_new_tab(self):
        action = [ActionModel(tool_name=Tools.BROWSER.value,
                              action_name=BrowserAction.NEW_TAB.value.name,
                              params={"url": "https://www.baidu.com"})]
        ob, _, _, _, info = self.browser_tool.step(action)
        self.assertEqual(info, {'exception': ''})

    def test_goto_url(self):
        action = [ActionModel(tool_name=Tools.BROWSER.value,
                              action_name=BrowserAction.GO_TO_URL.value.name,
                              params={"url": "test.json"})]
        ob, _, _, _, info = self.browser_tool.step(action)
        self.assertEqual(info, {'exception': ''})
        import time
        time.sleep(10)
