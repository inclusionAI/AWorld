# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import unittest

from aworld.logs.util import logger
from aworld.virtual_environments.gym.openai_gym import OpenAIGym


class OpenAIGymTest(unittest.TestCase):
    def setUp(self):
        self.gym = OpenAIGym('CartPole-v0', [], render=False)

    def tearDown(self):
        self.gym.close()

    def render(self):
        self.gym.render()

    def test_reset(self):
        state = self.gym.reset()
        self.assertEqual(len(state), 4)

        self.gym.reset()
        state, reward, terminal, _, _ = self.gym.step(0)
        transform_state = self.gym.transform_state(state)
        action = [0.1, 0.1, 0.1, -0.1]
        transform_action = self.gym.transform_action(action)


    def test_gym(self):
        self.gym.reset()
        state, reward, terminal, _, _ = self.gym.step(0)
        transform_state = self.gym.transform_state(state)

        logger.info(transform_state)
        self.assertEqual(len(state), 4)
        self.assertTrue((state == transform_state).all())

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
        self.assertEqual(transform_state, {'gymtpl1': 0.1, 'gymtpl3': -0.1, 'gymtpl0': 0.1, 'gymtpl2': 0.1})

        state = tuple([{'transform-state': {'inner': [0.1, 0.1, 0.1, -0.1]}}])
        transform_state = self.gym.transform_state(state)
        self.assertEqual(transform_state, {'gymtpl0-transform-state-inner': [0.1, 0.1, 0.1, -0.1]})

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
