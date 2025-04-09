# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import subprocess
import os
import signal
import sys
from typing import Any, Dict, Tuple, List

from aworld.config.common import Tools
from aworld.config.conf import ToolConfig
from aworld.config.tool_action import ShellAction
from aworld.core.common import ActionModel, Observation, ActionResult
from aworld.core.envs.tool import Tool, AgentInput, ToolFactory
from aworld.logs.util import logger
from aworld.virtual_environments.utils import build_observation


@ToolFactory.register(name=Tools.SHELL.value,
                      desc="shell execute tool",
                      supported_action=ShellAction,
                      conf_file_name=f'{Tools.SHELL.value}_tool.yaml')
class ShellTool(Tool[Observation, List[ActionModel]]):
    """
    used to execute shell commands, providing initialization, execution, and exit functions.
    """

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """
        Initialize the ShellTool
        Args:
            conf: tool config
            **kwargs: -
        """
        super(ShellTool, self).__init__(conf, **kwargs)
        self.type = "function"
        self.working_dir = self.conf.get('working_dir')
        self.env = self.conf.get('env') if self.conf.get('env') else os.environ.copy()
        self.processes = []

    def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        AgentInput, dict[str, Any]]:
        """
        Reset the executor
        Args:
            seed: -
            options: -

        Returns:
            AgentInput, dict[str, Any]: -
        """
        self.working_dir = None
        self.env = os.environ.copy()
        self.processes = []
        self._finished = False
        return build_observation(observer=self.name(),
                                 ability=ShellAction.EXECUTE_SCRIPT.value.name), {}

    def close(self) -> None:
        """
        Close the executor
        Returns:
            None
        """
        try:
            for process in self.processes:
                # Check whether the process is still running
                if process.poll() is None:
                    try:
                        # Try to gracefully terminate the process
                        if sys.platform != "win32":
                            os.kill(process.pid, signal.SIGTERM)
                        else:
                            process.terminate()
                    except Exception as e:
                        logger.warning(f"An error occurred while terminating the process. e: {str(e)}")
        except Exception as e:
            logger.warning(f"Error while exiting Shell Executor. e: {str(e)}")
        finally:
            # Clear process list
            self.processes = []
            self._finished = True

    def step(self,
             actions: list[ActionModel],
             **kwargs) -> Tuple[Observation, float, bool, bool, dict[str, Any]]:
        """
        Step the executor
        Args:
            actions: actions
            **kwargs: -
        Returns:
            Observation, float, bool, bool, dict[str, Any]: -
        """
        self._finished = False
        reward = 0
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability=ShellAction.EXECUTE_SCRIPT.value.name)
        try:
            if not actions:
                return (observation, reward,
                        kwargs.get("terminated",
                                   False), kwargs.get("truncated", False), {
                            "exception": "actions is empty"
                        })

            for action in actions:
                cmd_string = action.params.get("command", "")
                if not cmd_string:
                    continue
                _, output, error = self.execute(cmd_string)

                observation.content = output
                observation.action_result.append(
                    ActionResult(is_done=True,
                                 success=False if error else True,
                                 content=output,
                                 error=error,
                                 keep=False))
            reward = 1
        except Exception as e:
            fail_error = str(e)
        finally:
            self._finished = True

        info = {"exception": fail_error}
        info.update(kwargs)
        return (observation,
                reward,
                kwargs.get("terminated", False),
                kwargs.get("truncated", False),
                info)

    def execute(self, script: str, capture_output: bool = True, timeout: int = 5):
        """
        exec shell script
        Args:
            script (str): shell script to execute
            capture_output (bool): whether to capture the script output
            timeout (int, optional): Command execution timeout (seconds)
        Returns:
            dict: action result
        """
        try:
            if capture_output:
                process_ = subprocess.run(
                    script,
                    shell=True,
                    cwd=self.working_dir,
                    env=self.env,
                    timeout=timeout,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True
                )

                return {
                    'success': process_.returncode == 0,
                    'return_code': process_.returncode,
                    'stdout': process_.stdout,
                    'stderr': process_.stderr,
                    'script': script
                }
            else:
                process_ = subprocess.Popen(
                    script,
                    shell=True,
                    cwd=self.working_dir,
                    env=self.env
                )
                self.processes.append(process_)
                process_.wait(timeout=timeout)

                return {
                    'success': process_.returncode == 0,
                    'return_code': process_.returncode,
                    'script': script
                }

        except subprocess.TimeoutExpired:
            return {
                'success': False,
                'error': 'Timeout',
                'script': script
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'script': script
            }

    def execute_async(self, script: str):
        """
        Execute shell script asynchronously (no waiting)
        Args:
            script (str): The shell script to execute
        Returns:
            subprocess.Popen: Process object
        """
        try:
            process_ = subprocess.Popen(
                script,
                shell=True,
                cwd=self.working_dir,
                env=self.env
            )
            self.processes.append(process_)
            return process_
        except Exception as e:
            logger.warning(f"An error occurred while executing the script asynchronously. e: {str(e)}")
            return None
