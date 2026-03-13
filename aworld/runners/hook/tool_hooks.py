# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.runners.hook.hooks import Hook, HookPoint


class OnStartToolCallHook(Hook):
    def point(self):
        return HookPoint.ON_START_TOOL_CALL


class OnFinishedToolCallHook(Hook):
    def point(self):
        return HookPoint.ON_FINISHED_TOOL_CALL


class OnToolCallHook(Hook):
    def point(self):
        return HookPoint.ON_TOOL_CALL


class OnSuccessToolCallHook(Hook):
    def point(self):
        return HookPoint.ON_SUCCESS_TOOL_CALL


class OnErrorToolCallHook(Hook):
    def point(self):
        return HookPoint.ON_ERROR_TOOL_CALL
