# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import unittest

from examples.tools.common import Tools
from aworld.core.common import ActionModel

from aworld.core.tool.base import ToolFactory
from aworld.logs.util import logger


class OpenAIGymTest(unittest.TestCase):
    def setUp(self):
        # self.gym = OpenAIGym('CartPole-v0', [], render=False)
        self.gym = ToolFactory(Tools.GYM.value)

    def tearDown(self):
        self.gym.close()

    def render(self):
        self.gym.render()

    def test_reset(self):
        state = self.gym.reset()
        self.assertEqual(len(state), 2)

        self.gym.reset()

        message = self.gym.step([ActionModel(action_name='play', params={'result': 0})])
        state, reward, terminated, truncated, info = message.payload
        transform_state = self.gym.transform_state(state)
        action = [0.1, 0.1, 0.1, -0.1]
        transform_action = self.gym.transform_action(action)

    def test_gym(self):
        self.gym.reset()
        message = self.gym.step([ActionModel(action_name='play', params={'result': 0})])
        state, reward, terminated, truncated, info = message.payload
        transform_state = self.gym.transform_state(state)

        logger.info(transform_state)
        self.assertTrue((state == transform_state))

    def test_dim(self):
        self.assertEqual(self.gym._state_dim(), 4)
        self.assertEqual(self.gym._action_dim(), 2)

    def test_transform_state(self):
        state = {'transform': [0.1, 0.1, 0.1, -0.1]}
        transform_state = self.gym.transform_state(state)
        self.assertEqual(transform_state, state)

        state = {'nest1': {'nest2': {'transform': [0.1, 0.1, 0.1, -0.1]}}}
        transform_state = self.gym.transform_state(state)
        self.assertEqual(transform_state, {'nest1-nest2-transform': [0.1, 0.1, 0.1, -0.1]})

        state = tuple([0.1, 0.1, 0.1, -0.1])
        transform_state = self.gym.transform_state(state)
        self.assertEqual(transform_state, {'gym0': 0.1, 'gym1': 0.1, 'gym2': 0.1, 'gym3': -0.1})

        state = tuple([{'transform-state': {'inner': [0.1, 0.1, 0.1, -0.1]}}])
        transform_state = self.gym.transform_state(state)
        self.assertEqual(transform_state, {'gym0-transform-state-inner': [0.1, 0.1, 0.1, -0.1]})

    def test_transform_action(self):
        action = [0.1, 0.1, 0.1, -0.1]
        transform_action = self.gym.transform_action(action)
        self.assertEqual(transform_action, action)

        action = {'transform': [0.1, 0.1, 0.1, -0.1]}
        transform_action = self.gym.transform_action(action)
        self.assertEqual(transform_action, action)

        action = {'nest1': {'nest2': {'transform': [0.1, 0.1, 0.1, -0.1]}}}
        transform_action = self.gym.transform_state(action)
        self.assertEqual(transform_action, {'nest1-nest2-transform': [0.1, 0.1, 0.1, -0.1]})

        action = tuple([0.1, 0.1, 0.1, -0.1])
        transform_action = self.gym.transform_action(action)
        self.assertEqual(transform_action, (0.1, 0.1, 0.1, -0.1))

        action = {'transform-action': [0.1, 0.1, 0.1, -0.1]}
        transform_action = self.gym.transform_action(action)
        self.assertEqual(transform_action, {'transform': {'action': [0.1, 0.1, 0.1, -0.1]}})

        action = {'transform-action': {'inner': [0.1, 0.1, 0.1, -0.1]}}
        transform_action = self.gym.transform_action(action)
        self.assertEqual(transform_action, {'transform': {'action': {'inner': [0.1, 0.1, 0.1, -0.1]}}})
