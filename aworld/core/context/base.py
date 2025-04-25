# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from collections import Counter
from typing import Dict

from pydantic import BaseModel

from aworld.config import ConfigDict
from aworld.core.context.session import Session
from aworld.core.singleton import InheritanceSingleton
from aworld.core.task import Config
from aworld.logs.util import logger


class Context(InheritanceSingleton):
    def __init__(self, conf: Config = None, user: str = None, **kwargs):
        self.conf = conf
        if isinstance(conf, ConfigDict):
            pass
        elif isinstance(conf, Dict):
            self.conf = ConfigDict(conf)
        elif isinstance(conf, BaseModel):
            # To add flexibility
            self.conf = ConfigDict(conf.model_dump())
        else:
            logger.warning(f"Unknown conf type: {type(conf)}")

        self._user = user
        self._task_id = kwargs.get('task_id', self.conf.get('task_id', None))
        self._engine = kwargs.get('engine', self.conf.get('engine', None))
        self.session: Session = None
        self.token_usage = {}

    def add_token(self, usage: Dict[str, int]):
        self.token_usage = dict(Counter(usage) + Counter(self.token_usage))

    @property
    def engine(self):
        return self._engine

    @property
    def user(self):
        if self._user is None:
            self._user = self.conf.get('user', None)
        return self._user

    @user.setter
    def user(self, user):
        if user is not None:
            self._user = user

    @property
    def task_id(self):
        return self._task_id

    @task_id.setter
    def task_id(self, task_id):
        if task_id is not None:
            self._task_id = task_id

    @property
    def session_id(self):
        if self.session:
            return self.session.session_id

    @property
    def record_path(self):
        return "."

    @property
    def is_task(self):
        return True

    @property
    def enable_visible(self):
        return False

    @property
    def enable_failover(self):
        return False

    @property
    def enable_cluster(self):
        return False
