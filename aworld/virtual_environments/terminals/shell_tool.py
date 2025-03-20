# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import subprocess
import os
import signal
import sys
from typing import Any, Dict, Tuple, List

from aworld.config.conf import ToolConfig
from aworld.core.envs.tool_action import ShellAction
from aworld.core.common import ActionModel, Observation, ActionResult, Tools
from aworld.core.envs.env_tool import EnvTool, AgentInput, ToolFactory


@ToolFactory.register(name=Tools.SHELL.value, desc="shell execute tool", supported_action=ShellAction)
class ShellTool(EnvTool[Observation, List[ActionModel]]):
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
        self.working_dir = self.dict_conf.get('working_dir')
        self.env = self.dict_conf.get('env') if self.dict_conf.get('env') else os.environ.copy()
        self.processes = []
        self.step_finished = True

    def name(self):
        """
        Get the name of the tool
        Args:
            -
        Returns:
            str: tool name
        """
        return Tools.SHELL.value

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
        self.step_finished = True
        return None, {}

    def finished(self) -> bool:
        """
        Check if the executor is finished
        Args:
            -
        Returns:
            bool: True if finished, False otherwise
        """
        return self.step_finished

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
                        print(f"An error occurred while terminating the process. e: {str(e)}")
        except Exception as e:
            print(f"Error while exiting Shell Executor. e: {str(e)}")
        finally:
            # Clear process list
            self.processes = []
            self.step_finished = True

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
        self.step_finished = False
        reward = 0
        fail_error = ""
        observation: 'Observation' = Observation(**{
            'dom_tree': '',
            'image': '',
            'action_result': [],
            'info': {}
        })
        try:
            if not actions:
                return (self.cur_observation, reward,
                        kwargs.get("terminated",
                                   False), kwargs.get("truncated", False), {
                            "exception": "actions is empty"
                        })
            for action in actions:
                cmd_string = action.params.get("command", "")
                if not cmd_string:
                    continue
                _, output, error = self.execute(cmd_string)
                observation.action_result.append(
                    ActionResult(is_done=True,
                                 success=False if error else True,
                                 content=output,
                                 error=error,
                                 include_in_memory=False))
            reward = 1
        except Exception as e:
            fail_error = str(e)
        finally:
            self.step_finished = True

        return (observation, reward, kwargs.get("terminated", False),
                kwargs.get("truncated", False), {
                    "exception": fail_error
                })

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
            print(f"An error occurred while executing the script asynchronously. e: {str(e)}")
            return None


if __name__ == "__main__":
    shell = ShellTool()

    # Execute script and capture output
    result = shell.execute("whoami")
    if result['success']:
        print("stdout:", result['stdout'])
    else:
        print("stderr:", result['stderr'])

    # Execute script with environment variables
    custom_env = os.environ.copy()
    custom_env['CUSTOM_VAR'] = 'custom_value'
    shell_with_env = ShellTool(env=custom_env)
    result = shell_with_env.execute("echo $CUSTOM_VAR")
    print("stdout:", result['stdout'])

    # Asynchronously executing long-running scripts
    process = shell.execute_async("sleep 5 && echo 'Async command completed'")

    # Execute script in specific directory
    temp_dir = "/tmp"
    shell_with_dir = ShellTool(working_dir=temp_dir)
    result = shell_with_dir.execute("pwd")
    print(f"Results of executing pwd in {temp_dir}::", result['stdout'])

    # Execute multiple scripts
    scripts = [
        "ls -la",
        "whoami",
        "date"
    ]

    for cmd in scripts:
        result = shell.execute(cmd)
        print(f"\nexecute '{cmd}':")
        print(result['stdout'])

    # Exit Executor
    shell.close()
    shell_with_env.close()
    shell_with_dir.close()
