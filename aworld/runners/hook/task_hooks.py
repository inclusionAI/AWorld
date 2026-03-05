# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from aworld.runners.hook.hooks import Hook, HookPoint


class OnRunHook(Hook):
    def point(self):
        return HookPoint.ON_RUN_TASK


class OnSuccessHook(Hook):
    def point(self):
        return HookPoint.ON_SUCCESS_TASK


class OnErrorHook(Hook):
    def point(self):
        return HookPoint.ON_ERROR_TASK


class OnStartHook(Hook):
    def point(self):
        return HookPoint.ON_START_TASK


class OnFinishHook(Hook):
    def point(self):
        return HookPoint.ON_FINISHED_TASK
