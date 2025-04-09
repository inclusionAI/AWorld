# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import pytest
from unittest.mock import patch

from aworld.config.common import Tools
from aworld.core.common import ActionModel
from aworld.core.envs.tool import ToolFactory


class TestPythonTool:
    @pytest.fixture
    def python_tool(self):
        return ToolFactory(Tools.PYTHON_EXECUTE.value)

    def test_name(self, python_tool):
        assert python_tool.name() == Tools.PYTHON_EXECUTE.value

    def test_extract_imports(self, python_tool):
        code = """
        import numpy
        from pandas import DataFrame
        import os, sys
        from scipy import stats
        """
        imports = python_tool.extract_imports(code)
        expected = {"numpy", "pandas", "os", "sys", "scipy"}
        assert imports == expected

    @patch('subprocess.check_call')
    def test_install_dependencies(self, mock_check_call, python_tool):
        packages = {"numpy", "pandas"}
        with patch('builtins.__import__', side_effect=ImportError):
            python_tool.install_dependencies(packages)
        assert mock_check_call.call_count == 2
        assert python_tool.installed_packages == packages

    @patch('subprocess.check_call')
    def test_uninstall_dependencies(self, mock_check_call, python_tool):
        python_tool.installed_packages = {"numpy", "pandas"}
        python_tool.uninstall_dependencies()
        assert mock_check_call.call_count == 2
        assert len(python_tool.installed_packages) == 0

    def test_reset(self, python_tool):
        python_tool.local_namespace = {"test": 1}
        python_tool.global_namespace = {"test": 2}
        python_tool.installed_packages = {"numpy"}

        python_tool.reset()

        assert python_tool.local_namespace == {}
        assert python_tool.global_namespace == {}
        assert python_tool.finished == False
        assert len(python_tool.installed_packages) == 0

    def test_finished(self, python_tool):
        python_tool._finished = True
        assert python_tool.finished == True

        python_tool._finished = False
        assert python_tool.finished == False

    def test_execute_success(self, python_tool):
        code = """x = 1 + 2
print('Hello')
x
"""
        result, output, error = python_tool.execute(code)
        assert output.strip() == "Hello"
        assert error is None

    def test_step(self, python_tool):
        actions = [
            ActionModel(
                action_name="python",
                params={"code": "print('test')\n2+2"}
            )
        ]

        observation, reward, terminated, truncated, info = python_tool.step(actions)

        assert reward == 1
        assert len(observation.action_result) == 1
        assert observation.action_result[0].success == True
        assert "test" in observation.action_result[0].content
        assert observation.action_result[0].error == "None"

    def test_close(self, python_tool):
        python_tool.local_namespace = {"test": 1}
        python_tool.global_namespace = {"test": 2}
        python_tool.installed_packages = {"numpy"}

        python_tool.close()

        assert python_tool.local_namespace == {}
        assert python_tool.global_namespace == {}
        assert python_tool.finished == True
