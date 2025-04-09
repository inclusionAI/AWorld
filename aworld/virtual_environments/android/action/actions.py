# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import json

from aworld.config.common import Tools
from aworld.config.tool_action import AndroidAction
from aworld.core.envs.action_factory import ActionFactory
from aworld.core.common import ActionModel, ActionResult
from aworld.virtual_environments.android.action.adb_controller import ADBController
from aworld.virtual_environments.android.config.android_action_space import AndroidActionParamEnum
from aworld.virtual_environments.action import ExecutableAction


@ActionFactory.register(name=AndroidAction.TAP.value.name,
                        desc=AndroidAction.TAP.value.desc,
                        tool_name=Tools.ANDROID.value)
class Tap(ExecutableAction):
    def act(self, action: ActionModel, **kwargs) -> ActionResult:
        controller: ADBController = kwargs.get('controller')
        tap_index = action.params[AndroidActionParamEnum.TAP_INDEX.value]
        if tap_index is None:
            raise Exception(f'Invalid action: {action}')
        controller.tap(tap_index)
        return ActionResult(content="", keep=True)


@ActionFactory.register(name=AndroidAction.INPUT_TEXT.value.name,
                        desc=AndroidAction.INPUT_TEXT.value.desc,
                        tool_name=Tools.ANDROID.value)
class InputText(ExecutableAction):
    def act(self, action: ActionModel, **kwargs) -> ActionResult:
        controller: ADBController = kwargs.get('controller')
        input_text = action.params[AndroidActionParamEnum.INPUT_TEXT.value]
        if input_text is None:
            raise Exception(f'Invalid action: {action}')
        controller.text(input_text)
        return ActionResult(content="", keep=True)


@ActionFactory.register(name=AndroidAction.LONG_PRESS.value.name,
                        desc=AndroidAction.LONG_PRESS.value.desc,
                        tool_name=Tools.ANDROID.value)
class LongPress(ExecutableAction):
    def act(self, action: ActionModel, **kwargs) -> ActionResult:
        controller: ADBController = kwargs.get('controller')
        long_press_index = action.params[AndroidActionParamEnum.LONG_PRESS_INDEX.value]
        if long_press_index is None:
            raise Exception(f'Invalid action: {action}')
        controller.long_press(long_press_index)
        return ActionResult(content="", keep=True)


@ActionFactory.register(name=AndroidAction.SWIPE.value.name,
                        desc=AndroidAction.SWIPE.value.desc,
                        tool_name=Tools.ANDROID.value)
class Swipe(ExecutableAction):
    def act(self, action: ActionModel, **kwargs) -> ActionResult:
        controller: ADBController = kwargs.get('controller')
        swipe_start_index = action.params[AndroidActionParamEnum.SWIPE_START_INDEX.value]
        direction = action.params[AndroidActionParamEnum.DIRECTION.value]
        dist = action.params.get(AndroidActionParamEnum.DIST.value, None)
        if swipe_start_index is None or direction is None:
            raise Exception(f'Invalid action: {action}')
        if dist:
            controller.swipe(swipe_start_index, direction, dist)
        else:
            controller.swipe(swipe_start_index, direction)
        return ActionResult(content="", keep=True)


@ActionFactory.register(name=AndroidAction.DONE.value.name,
                        desc=AndroidAction.DONE.value.desc,
                        tool_name=Tools.ANDROID.value)
class Done(ExecutableAction):
    def act(self, action: ActionModel, **kwargs) -> ActionResult:
        output_dict = action.model_dump(exclude={'success'})
        return ActionResult(is_done=True, success=True, content=json.dumps(output_dict))
