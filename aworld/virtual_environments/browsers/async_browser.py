# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import asyncio
import base64
import json
import os
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Tuple, List

from aworld.core.envs.tool_action import BrowserAction
from aworld.core.common import Observation, ActionModel, ActionResult, Tools
from aworld.logs.util import logger
from aworld.core.envs.tool import action_executor, ToolFactory, AsyncTool
from aworld.virtual_environments.browsers.action.executor import BrowserToolActionExecutor
from aworld.virtual_environments.browsers.util.dom import DomTree
from aworld.virtual_environments.conf import BrowserToolConfig
from aworld.virtual_environments.browsers.util.dom_build import async_build_dom_tree
from aworld.utils import import_package
from aworld.virtual_environments.utils import build_observation

URL_MAX_LENGTH = 4096
UTF8 = "".join(chr(x) for x in range(0, 55290))
ASCII = "".join(chr(x) for x in range(32, 128))


@ToolFactory.register(name=Tools.BROWSER.value,
                      desc="browser",
                      asyn=True,
                      supported_action=BrowserAction,
                      conf_file_name=f'{Tools.BROWSER.value}_tool.yaml')
class BrowserTool(AsyncTool[Observation, List[ActionModel]]):
    def __init__(self, conf: BrowserToolConfig, **kwargs) -> None:
        super(BrowserTool, self).__init__(conf)

        self.initialized = False
        self._finish = False
        self.record_trace = self.conf.get("working_dir", False)
        self.sleep_after_init = self.conf.get("sleep_after_init", False)
        self.js_code = resources.read_text('virtual_environments.browsers.script', 'buildDomTree.js')
        self.cur_observation = None
        import_package("playwright")

    async def init(self) -> None:
        from playwright.async_api import async_playwright

        self.context_manager = async_playwright()
        self.playwright = await self.context_manager.start()

        self.browser = await self._create_browser()
        self.context = await self._create_browser_context()

        if self.record_trace:
            await self.context.tracing.start(screenshots=True, snapshots=True)

        self.page = await self.context.new_page()
        if self.conf.get("custom_executor"):
            self.action_executor = BrowserToolActionExecutor(self)
        else:
            self.action_executor = action_executor
        self.initialized = True

    async def _create_browser(self):
        browse_name = self.conf.get("browse_name", "chromium")
        browse = getattr(self.playwright, browse_name)
        cdp_url = self.conf.get("cdp_url")
        wss_url = self.conf.get("wss_url")
        if cdp_url:
            if browse_name != "chromium":
                logger.warning(f"{browse_name} unsupported CDP, will use chromium browser")
                browse = self.playwright.chromium
            logger.info(f"Connecting to remote browser via CDP {cdp_url}")
            browser = await browse.connect_over_cdp(cdp_url)
        elif wss_url:
            logger.info(f"Connecting to remote browser via wss {wss_url}")
            browser = await browse.connect(wss_url)
        else:
            headless = self.conf.get("headless", False)
            slow_mo = self.conf.get("slow_mo", 0)
            disable_security_args = []
            if self.conf.get('disable_security', False):
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
            browser = await browse.launch(
                headless=headless,
                slow_mo=slow_mo,
                args=args,
                proxy=self.conf.get('proxy'),
            )
        return browser

    async def _create_browser_context(self):
        """Creates a new browser context with anti-detection measures and loads cookies if available."""
        from playwright.async_api import ViewportSize

        browser = self.browser
        if self.conf.get("cdp_url") and len(browser.contexts) > 0:
            context = browser.contexts[0]
        else:
            viewport_size = ViewportSize(width=self.conf.get("width", 1280),
                                         height=self.conf.get("height", 720))
            disable_security = self.conf.get('disable_security', False)

            context = await browser.new_context(viewport=viewport_size,
                                                no_viewport=False,
                                                user_agent=self.conf.get('user_agent'),
                                                java_script_enabled=True,
                                                bypass_csp=disable_security,
                                                ignore_https_errors=disable_security,
                                                record_video_dir=self.conf.get('working_dir'),
                                                record_video_size=viewport_size,
                                                locale=self.conf.get('locale'),
                                                storage_state=self.conf.get("storage_state", None),
                                                geolocation=self.conf.get("geolocation", None),
                                                device_scale_factor=1)
            if "chromium" == self.conf.get("browse_name", "chromium"):
                await context.grant_permissions(['camera', 'microphone'])

        if self.conf.get('trace_path'):
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)

        cookie_file = self.conf.get('cookies_file')
        if cookie_file and os.path.exists(cookie_file):
            with open(cookie_file, 'r') as read:
                cookies = json.loads(read.read())
                await context.add_cookies(cookies)
                logger.info(f'Cookies load from {cookie_file} finished')

        if self.conf.get('private'):
            js = resources.read_text("virtual_environments.browsers.script", "stealth.min.js")
            await context.add_init_script(js)

        return context

    async def get_cur_page(self):
        return self.page

    async def screenshot(self, full_page: bool = False) -> str:
        """Returns a base64 encoded screenshot of the current page.

        Args:
            full_page: When true, takes a screenshot of the full scrollable page, instead of the currently visible viewport.

        Returns:
            Base64 of the page screenshot
        """
        page = await self.get_cur_page()

        try:
            await page.bring_to_front()
            await page.wait_for_load_state(timeout=2000)
        except:
            logger.warning("bring to front load timeout")
            pass

        screenshot = await page.screenshot(
            full_page=full_page,
            animations='disabled',
            timeout=600000
        )
        logger.info("page screenshot finished")
        screenshot_base64 = base64.b64encode(screenshot).decode('utf-8')
        return screenshot_base64

    async def _get_observation(self) -> Observation:
        dom_tree = await self._parse_dom_tree()
        image = await self.screenshot()
        pixels_above, pixels_below = await self._scroll_info()
        info = {"pixels_above": pixels_above,
                "pixels_below": pixels_below,
                "url": self.page.url}
        return Observation(observer=self.name(), dom_tree=dom_tree, image=image, info=info)

    async def _parse_dom_tree(self) -> DomTree:
        args = {
            'doHighlightElements': self.conf.get("do_highlight", True),
            'focusHighlightIndex': self.conf.get("focus_highlight", -1),
            'viewportExpansion': self.conf.get("viewport_expansion", 0),
            'debugMode': logger.getEffectiveLevel() == 10,
        }
        element_tree, element_map = await async_build_dom_tree(self.page, self.js_code, args)
        return DomTree(element_tree=element_tree, element_map=element_map)

    async def _scroll_info(self) -> tuple[int, int]:
        """Get scroll position information for the current page."""
        scroll_y = await self.page.evaluate('window.scrollY')
        viewport_height = await self.page.evaluate('window.innerHeight')
        total_height = await self.page.evaluate('document.documentElement.scrollHeight')
        pixels_above = scroll_y
        pixels_below = total_height - (scroll_y + viewport_height)
        return pixels_above, pixels_below

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, Dict[str, Any]]:
        await super().reset(seed=seed, options=options)

        await self.close()
        await self.init()

        if self.sleep_after_init > 0:
            await asyncio.sleep(self.sleep_after_init)

        observation = await self._get_observation()
        observation.action_result = [ActionResult(content='start', keep=True)]
        self.cur_observation = observation
        return observation, {}

    async def save_trace(self, trace_path: str | Path) -> None:
        if self.record_trace:
            await self.context.tracing.stop(path=trace_path)

    @property
    async def finished(self) -> bool:
        return self._finish

    async def close(self) -> None:
        if hasattr(self, 'context') and self.context:
            await self.context.close()
        if hasattr(self, 'browser') and self.browser:
            await self.browser.close()
        if self.initialized:
            await self.context_manager.__aexit__()

    async def step(self, action: List[ActionModel], **kwargs) -> Tuple[
        Observation, float, bool, bool, Dict[str, Any]]:
        if not self.initialized:
            raise RuntimeError("Call init first before calling step.")

        if not action:
            logger.warning(f"{self.name()} has no action")
            return build_observation(observer=self.name(), ability='', content='no action'), 0., False, False, {}

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
            action_result, self.page = await self.action_executor.async_execute_action(action,
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

        contains_write_to_file = any(act.action_name == BrowserAction.WRITE_TO_FILE.value.name for act in action if act)
        if contains_write_to_file:
            msg = ""
            for action_result_elem in action_result:
                msg = action_result_elem.content
            # write_to_file observation
            return (Observation(content=msg, action_result=action_result, info=info),
                    reward,
                    terminated,
                    kwargs.get("truncated", False),
                    info)
        elif fail_error:
            # failed error observation
            return (Observation(),
                    reward,
                    terminated,
                    kwargs.get("truncated", False),
                    info)
        else:
            # normal observation
            observation = await self._get_observation()
            observation.ability = action[-1].action_name
            observation.action_result = action_result
            self.cur_observation = observation
            return (observation,
                    reward,
                    terminated,
                    kwargs.get("truncated", False),
                    info)
