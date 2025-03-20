# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import base64
import json
import os
import time
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Tuple, List

from aworld.core.env.tool_action import BrowserAction
from aworld.core.common import Observation, ActionModel, ActionResult, Tools
from aworld.logs.util import logger
from aworld.core.env.env_tool import action_executor, ToolFactory
from aworld.core.env.env_tool import EnvTool
from aworld.virtual_environments.browsers.action.executor import BrowserToolActionExecutor
from aworld.virtual_environments.browsers.util.dom import DomTree
from aworld.virtual_environments.conf import BrowserToolConfig
from aworld.virtual_environments.browsers.util.dom_build import build_dom_tree

URL_MAX_LENGTH = 4096
UTF8 = "".join(chr(x) for x in range(0, 55290))
ASCII = "".join(chr(x) for x in range(32, 128))


@ToolFactory.register(name=Tools.BROWSER.value, desc="browser", supported_action=BrowserAction)
class BrowserTool(EnvTool[Observation, List[ActionModel]]):
    def __init__(self, conf: BrowserToolConfig, **kwargs) -> None:
        super(BrowserTool, self).__init__(conf)

        self.initialized = False
        self._finish = False
        self.record_trace = self.dict_conf.get("record_trace", False)
        self.sleep_after_init = self.dict_conf.get("sleep_after_init", False)
        self.js_code = resources.read_text('aworld.virtual_environments.browsers.config', 'buildDomTree.js')
        self.cur_observation = None

    def name(self):
        return Tools.BROWSER.value

    def init(self) -> None:
        from playwright.sync_api import sync_playwright

        self.context_manager = sync_playwright()
        self.playwright = self.context_manager.start()

        self.browser = self._create_browser()
        self.context = self._create_browser_context()

        if self.record_trace:
            self.context.tracing.start(screenshots=True, snapshots=True)

        self.page = self.context.new_page()
        if self.dict_conf.get("use_browser_executor"):
            self.action_executor = BrowserToolActionExecutor(self)
        else:
            self.action_executor = action_executor
        self.initialized = True

    def _create_browser(self):
        browse_name = self.dict_conf.get("browse_name", "chromium")
        browse = getattr(self.playwright, browse_name)
        cdp_url = self.dict_conf.get("cdp_url")
        wss_url = self.dict_conf.get("wss_url")
        if cdp_url:
            if browse_name != "chromium":
                logger.warning(f"{browse_name} unsupported CDP, will use chromium browser")
                browse = self.playwright.chromium
            logger.info(f"Connecting to remote browser via CDP {cdp_url}")
            browser = browse.connect_over_cdp(cdp_url)
        elif wss_url:
            logger.info(f"Connecting to remote browser via wss {wss_url}")
            browser = browse.connect(wss_url)
        else:
            headless = self.dict_conf.get("headless", False)
            slow_mo = self.dict_conf.get("slow_mo", 0)
            disable_security_args = []
            if self.dict_conf.get('disable_security', False):
                disable_security_args = ['--disable-web-security',
                                         '--disable-site-isolation-trials',
                                         '--disable-features=IsolateOrigins,site-per-process']
            args = ['--no-sandbox',
                    '--disable-crash-reporte',
                    '--disable-blink-features=AutomationControlled',
                    '--disable-infobars',
                    '--disable-background-timer-throttling',
                    '--disable-popup-blocking',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding',
                    '--disable-window-activation',
                    '--disable-focus-on-load',
                    '--no-first-run',
                    '--no-default-browser-check',
                    '--no-startup-window',
                    '--window-position=0,0',
                    '--window-size=1280,720'] + disable_security_args
            browser = browse.launch(
                headless=headless,
                slow_mo=slow_mo,
                args=args,
                proxy=self.dict_conf.get('proxy'),
            )
        return browser

    def _create_browser_context(self):
        """Creates a new browser context with anti-detection measures and loads cookies if available."""
        from playwright.sync_api import ViewportSize

        browser = self.browser
        if self.dict_conf.get("cdp_url") and len(browser.contexts) > 0:
            context = browser.contexts[0]
        else:
            viewport_size = ViewportSize(width=self.dict_conf.get("width", 1280),
                                         height=self.dict_conf.get("height", 720))
            disable_security = self.dict_conf.get('disable_security', False)

            context = browser.new_context(viewport=viewport_size,
                                          no_viewport=False,
                                          user_agent=self.dict_conf.get('user_agent'),
                                          java_script_enabled=True,
                                          bypass_csp=disable_security,
                                          ignore_https_errors=disable_security,
                                          record_video_dir=self.dict_conf.get('record_video_dir'),
                                          record_video_size=viewport_size,
                                          locale=self.dict_conf.get('locale'),
                                          storage_state=self.dict_conf.get("storage_state", None),
                                          geolocation=self.dict_conf.get("geolocation", None),
                                          device_scale_factor=1)
            if "chromium" == self.dict_conf.get("browse_name", "chromium"):
                context.grant_permissions(['camera', 'microphone'])

        if self.dict_conf.get('trace_path'):
            context.tracing.start(screenshots=True, snapshots=True, sources=True)

        cookie_file = self.dict_conf.get('cookies_file')
        if cookie_file and os.path.exists(cookie_file):
            with open(cookie_file, 'r') as read:
                cookies = json.loads(read.read())
                context.add_cookies(cookies)
                logger.info(f'Cookies load from {cookie_file} finished')

        if self.dict_conf.get('private'):
            js = resources.read_text("aworld.virtual_environments.browsers.config", "stealth.min.js")
            context.add_init_script(js)

        return context

    def get_cur_page(self):
        return self.page

    def screenshot(self, full_page: bool = False) -> str:
        """Returns a base64 encoded screenshot of the current page.

        Args:
            full_page: When true, takes a screenshot of the full scrollable page, instead of the currently visible viewport.

        Returns:
            Base64 of the page screenshot
        """
        page = self.get_cur_page()

        try:
            page.bring_to_front()
            page.wait_for_load_state(timeout=2000)
        except:
            logger.warning("bring to front load timeout")
            pass

        screenshot = page.screenshot(
            full_page=full_page,
            animations='disabled',
            timeout=600000
        )
        logger.info("page screenshot finished")
        screenshot_base64 = base64.b64encode(screenshot).decode('utf-8')
        return screenshot_base64

    def _get_observation(self) -> Observation:
        dom_tree = self._parse_dom_tree()
        image = self.screenshot()
        pixels_above, pixels_below = self._scroll_info()
        info = {"pixels_above": pixels_above,
                "pixels_below": pixels_below,
                "url": self.page.url}
        return Observation(dom_tree=dom_tree, image=image, info=info)

    def _parse_dom_tree(self) -> DomTree:
        args = {
            'doHighlightElements': self.dict_conf.get("do_highlight", True),
            'focusHighlightIndex': self.dict_conf.get("focus_highlight", -1),
            'viewportExpansion': self.dict_conf.get("viewport_expansion", 0),
            'debugMode': logger.getEffectiveLevel() == 10,
        }
        element_tree, element_map = build_dom_tree(self.page, self.js_code, args)
        return DomTree(element_tree=element_tree, element_map=element_map)

    def _scroll_info(self) -> tuple[int, int]:
        """Get scroll position information for the current page."""
        scroll_y = self.page.evaluate('window.scrollY')
        viewport_height = self.page.evaluate('window.innerHeight')
        total_height = self.page.evaluate('document.documentElement.scrollHeight')
        pixels_above = scroll_y
        pixels_below = total_height - (scroll_y + viewport_height)
        return pixels_above, pixels_below

    def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, Dict[str, Any]]:
        super().reset(seed=seed, options=options)

        if not self.initialized:
            self.close()
            self.init()

        if self.sleep_after_init > 0:
            time.sleep(self.sleep_after_init)

        observation = self._get_observation()
        observation.action_result = [ActionResult(content='start', keep=True)]
        self.cur_observation = observation
        return observation, {}

    def save_trace(self, trace_path: str | Path) -> None:
        if self.record_trace:
            self.context.tracing.stop(path=trace_path)

    @property
    def finished(self) -> bool:
        return self._finish

    def close(self) -> None:
        if hasattr(self, 'context') and self.context:
            self.context.close()
        if hasattr(self, 'browser') and self.browser:
            self.browser.close()
        if self.initialized:
            self.context_manager.__exit__()

    def step(self, action: List[ActionModel], **kwargs) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        if not self.initialized:
            raise RuntimeError("Call init first before calling step.")

        reward = 0
        fail_error = ""
        action_result = None

        invalid_acts: List[int] = []
        for i, act in enumerate(action):
            if act.tool_name != Tools.BROWSER.value:
                logger.warning(f"tool {act.tool_name} is not a browser!")
                invalid_acts.append(i)

        if invalid_acts:
            for i in invalid_acts:
                action[i] = None

        try:
            action_result, self.page = self.action_executor.execute_action(action,
                                                                      observation=self.cur_observation,
                                                                      **kwargs)
            reward = 1
        except Exception as e:
            fail_error = str(e)

        terminated = kwargs.get("terminated", False)
        if action_result:
            for res in action_result:
                if res.is_done:
                    terminated = res.is_done
                    self._finish = True

        info = {"exception": fail_error}
        observation = self._get_observation()
        observation.action_result = action_result
        self.cur_observation = observation
        return (observation,
                reward,
                terminated,
                kwargs.get("truncated", False),
                info)
