# coding: utf-8
# Copyright (c) 2025 inclusionAI.

from enum import Enum

from aworld.core.common import ToolActionInfo, ParamInfo


class ToolAction(Enum):
    @classmethod
    def get_value_by_name(cls, name: str) -> ToolActionInfo | None:
        members = cls.__members__
        name = name.upper()
        if name in members:
            return members[name].value
        return None


class ChatAction(ToolAction):
    """chat between agents """
    TASK_DONE = ToolActionInfo(name="TASK_DONE",
                               desc="Complete task - with return text and if the task is finished (success=True) or not yet  completly finished (success=False), because last step is reached")


class SearchAction(ToolAction):
    """Info search actions."""
    WIKI = ToolActionInfo(name="wiki",
                          input_params={"query": ParamInfo(name="query",
                                                           type="str",
                                                           required=True,
                                                           desc="wiki search query input.")},
                          desc="Search the entity in WikiPedia and return the summary of the required page, containing factual information about the given entity.")
    DUCK_GO = ToolActionInfo(name="duck_go",
                             input_params={"query": ParamInfo(name="query",
                                                              type="str",
                                                              required=True,
                                                              desc="duckduckgo search query input"),
                                           "source": ParamInfo(name="source",
                                                               type="str",
                                                               required=False,
                                                               desc="duckduckgo search query input.",
                                                               default_value="text"),
                                           "max_results": ParamInfo(name="max_results",
                                                                    type="int",
                                                                    required=False,
                                                                    desc="duckduckgo search query input.",
                                                                    default_value=5)},
                             desc="Use DuckDuckGo search engine to search information for the given query")
    GOOGLE = ToolActionInfo(name="google",
                            input_params={"query": ParamInfo(name="query",
                                                             type="str",
                                                             required=True,
                                                             desc="google search query input."),
                                          "num_result_pages": ParamInfo(name="num_result_pages",
                                                                        type="int",
                                                                        required=False,
                                                                        desc="google search query input.",
                                                                        default_value=5)},
                            desc="Use Google search engine to search information for the given query.")


class GymAction(ToolAction):
    PLAY = ToolActionInfo(name="play",
                          desc="step")


class BrowserAction(ToolAction):
    """Definition of Browser tool supported action."""
    NONE = ToolActionInfo(name="none",
                          desc="Do nothing")
    GO_TO_URL = ToolActionInfo(name="go_to_url",
                               input_params={"url": ParamInfo(name="url",
                                                              type="str",
                                                              required=True,
                                                              desc="got to url in page on browser.")},
                               desc="Navigate to URL in the current tab")
    INPUT_TEXT = ToolActionInfo(name="input_text",
                                input_params={"text": ParamInfo(name="text",
                                                                type="str",
                                                                required=True,
                                                                desc="input text in page on browser"),
                                              "index": ParamInfo(name="index",
                                                                 type="int",
                                                                 required=True,
                                                                 desc="index of click element in page on browser.")},
                                desc="Input text into a input interactive element")
    SEARCH = ToolActionInfo(name="search",
                            input_params={"url": ParamInfo(name="url",
                                                           type="str",
                                                           required=True,
                                                           desc="search url."),
                                          "query": ParamInfo(name="query",
                                                             type="str",
                                                             required=True,
                                                             desc="search query input in page on browser.")},
                            desc="Search the query in search engine, Google, Baidu etc., in the current tab, the query should be a search query like humans search in search engine, concrete and not vague or super long. More the single most important items. ")
    SEARCH_GOOGLE = ToolActionInfo(name="search_google",
                                   input_params={"url": ParamInfo(name="url",
                                                                  type="str",
                                                                  required=True,
                                                                  desc="search url."),
                                                 "query": ParamInfo(name="query",
                                                                    type="str",
                                                                    required=True,
                                                                    desc="search query input in google.")},
                                   desc="Search the query in Google in the current tab, the query should be a search query like humans search in Google, concrete and not vague or super long. More the single most important items. ")
    GO_BACK = ToolActionInfo(name="go_back",
                             desc="Go back")
    GO_FORWARD = ToolActionInfo(name="go_forward",
                                desc="Go forward")
    SCROLL_DOWN = ToolActionInfo(name="scroll_down",
                                 input_params={"amount": ParamInfo(name="amount",
                                                                   type="int",
                                                                   required=True,
                                                                   desc="pixel amount.")},
                                 desc="Scroll down the page by pixel amount - if no amount is specified, scroll down one page")
    SCROLL_UP = ToolActionInfo(name="scroll_up",
                               input_params={"amount": ParamInfo(name="amount",
                                                                 type="int",
                                                                 required=True,
                                                                 desc="pixel amount.")},
                               desc="Scroll up the page by pixel amount - if no amount is specified, scroll up one page")
    CLICK_ELEMENT = ToolActionInfo(name="click_element",
                                   input_params={"index": ParamInfo(name="index",
                                                                    type="int",
                                                                    required=True,
                                                                    desc="index of click element in page on browser.")},
                                   desc="Click element")
    SAYING = ToolActionInfo(name="saying")
    NEW_TAB = ToolActionInfo(name="new_tab",
                             input_params={"url": ParamInfo(name="url",
                                                            type="str",
                                                            required=True,
                                                            desc="Open url in new tab on browser.")},
                             desc="Open url in new tab")
    SWITCH_TAB = ToolActionInfo(name="switch_tab",
                                input_params={"page_id": ParamInfo(name="page_id",
                                                                   type="int",
                                                                   required=True,
                                                                   desc="Switch tab by page id on browser.")},
                                desc="Switch tab")
    OPEN_TAB = ToolActionInfo(name="open_tab",
                              input_params={"url": ParamInfo(name="url",
                                                             type="str",
                                                             required=True,
                                                             desc="Open url in new tab on browser.")},
                              desc="Open url in new tab")
    WAIT = ToolActionInfo(name="wait",
                          input_params={"seconds": ParamInfo(name="seconds",
                                                             type="int",
                                                             required=True,
                                                             desc="Open url in new tab on browser.")},
                          desc="Open url in new tab")
    EXTRACT_CONTENT = ToolActionInfo(name="extract_content",
                                     input_params={"goal": ParamInfo(name="goal",
                                                                     type="str",
                                                                     required=True,
                                                                     desc="The goal in page content.")},
                                     desc="Extract page content to retrieve specific information from the page, e.g. all company names, a specifc description, all information about, links with companies in structured format or simply links")
    DONE = ToolActionInfo(name="done",
                          desc="Complete task - with return text and if the task is finished (success=True) or not yet  completly finished (success=False), because last step is reached")


class AndroidAction(ToolAction):
    """Definition of android tool supported action."""
    TAP = ToolActionInfo(name="tap",
                         input_params={"tap_index": ParamInfo(name="tap_index",
                                                              type="int",
                                                              required=True,
                                                              desc="Index of tap element.")},
                         desc="Tap element")
    SWIPE = ToolActionInfo(name="swipe",
                           input_params={"index": ParamInfo(name="index",
                                                            type="int",
                                                            required=True,
                                                            desc="Index of swipe the screen."),
                                         "direction": ParamInfo(name="direction",
                                                                type="str",
                                                                required=True,
                                                                desc="Direction of swipe the screen."),
                                         "dist": ParamInfo(name="dist",
                                                           type="str",
                                                           required=True,
                                                           desc="Dist of swipe the screen.")},
                           desc="Swipe the screen")
    LONG_PRESS = ToolActionInfo(name="long_press",
                                input_params={"long_press_index": ParamInfo(name="long_press_index",
                                                                            type="int",
                                                                            required=True,
                                                                            desc="Index of the element.")},
                                desc="Long press the element")
    INPUT_TEXT = ToolActionInfo(name="input_text",
                                input_params={"input_text": ParamInfo(name="input_text",
                                                                      type="str",
                                                                      required=True,
                                                                      desc="Input text into a input interactive element.")},
                                desc="Input text into a input interactive element")
    DONE = ToolActionInfo(name="done",
                          input_params={"type": ParamInfo(name="type",
                                                          type="str",
                                                          required=True,
                                                          desc="Type of done."),
                                        "success": ParamInfo(name="success",
                                                             type="str",
                                                             required=True,
                                                             desc="Task success status.")},
                          desc="task done")


class FileAction(ToolAction):
    """Definition of file supported action."""
    OPEN = ToolActionInfo(name="open",
                          input_params={},
                          desc="")


class ImageAnalysisAction(ToolAction):
    """Definition of image analysis supported action."""
    ANALYSIS = ToolActionInfo(name="analysis",
                              input_params={},
                              desc="")


class CodeExecuteAction(ToolAction):
    """Definition of code execute supported action."""
    EXECUTE_CODE = ToolActionInfo(
        name="execute_code",
        input_params={"code": ParamInfo(name="code",
                                        type="str",
                                        required=True,
                                        desc="The input code to execute. Codes should be complete and runnable (like running a script), and need to explicitly use the print statement to get the output.")},
        desc="Execute the given codes. Codes should be complete and runnable (like running a script), and need to explicitly use the print statement to get the output.")


class ShellAction(ToolAction):
    """Definition of shell execute supported action."""
    EXECUTE_SCRIPT = ToolActionInfo(
        name="execute_script",
        input_params={"script": ParamInfo(name="script",
                                          type="str",
                                          required=True,
                                          desc="The input script to execute. Script should be complete and runnable, and need to explicitly use the print statement to get the output.")},
        desc="Execute the given script, need to explicitly use the print statement to get the output.")
