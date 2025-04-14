import sys
import ast
import re
import subprocess
from typing import Any, Dict, Tuple, List
from io import StringIO
from aworld.logs.util import logger
from aworld.config.conf import ToolConfig
from aworld.core.envs.tool_action import PythonToolAction
from aworld.core.common import ActionModel, Observation, ActionResult, Tools
from aworld.core.envs.tool import Tool, AgentInput, ToolFactory
from aworld.utils import import_package
import sys
sys.path.append('/Users/zhuige/Documents/llm/agent/projects/owl/owl/camel/interpreters/')
sys.path.append('/Users/zhuige/Documents/llm/agent/projects/owl/owl/')
from subprocess_interpreter import SubprocessInterpreter

@ToolFactory.register(name=Tools.PYTHON_EXECUTE.value, desc="python interpreter tool",
                      supported_action=PythonToolAction)
class PythonTool(Tool[Observation, List[ActionModel]]):

    def __init__(self,
                 conf: ToolConfig,
                 **kwargs) -> None:
        """
        Initialize the PythonExecutor
        Args:
            conf: tool config
            **kwargs: -
        Return:
            None
        """
        super(PythonTool, self).__init__(conf, **kwargs)
        self.type = "function"
        self.local_namespace = {}
        self.global_namespace = {}
        self.original_stdout = sys.stdout
        self.output_buffer = StringIO()
        self.step_finished = True
        self.installed_packages = set()
        import_package('langchain_experimental')
        from langchain_experimental.utilities.python import PythonREPL
        self.python_repl = PythonREPL()
        self.interpreter = SubprocessInterpreter(
            require_confirm=False,
            print_stdout=True,
            print_stderr=True,
        )

    def name(self):
        """
        Get the name of the tool
        Args:
            -
        Returns:
            str: tool name
        """
        return self.__class__.__name__

    def extract_imports(self, code: str) -> set:
        """
        Extract import statements
        Args:
            code: python code
        Returns:
            set: import statements
        """
        imports = set()

        try:
            tree = ast.parse(code)

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    # deal import xxx or import xxx as yyy
                    for name in node.names:
                        package_name = name.name.split('.')[0]
                        imports.add(package_name)

                elif isinstance(node, ast.ImportFrom):
                    # deal from xxx import yyy or from xxx.yyy import zzz
                    if node.module:
                        package_name = node.module.split('.')[0]
                        imports.add(package_name)

        except SyntaxError:
            import_pattern = r'^import\s+([\w\s,]+)|from\s+(\w+)'
            for line in code.split('\n'):
                line = line.strip()
                match = re.match(import_pattern, line)
                if match:
                    if match.group(1):

                        packages = [p.strip() for p in match.group(1).split(',')]
                        for package in packages:
                            if package:
                                package_name = package.split()[0]
                                imports.add(package_name)
                    elif match.group(2):
                        imports.add(match.group(2))

        return imports

    def install_dependencies(self,
                             packages: set) -> None:
        """
        Install dependency packages
        Args:
            packages: python third packages
        Returns:
            None
        """
        for package in packages:
            try:
                __import__(package)
            except ImportError:
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "install", package])
                    self.installed_packages.add(package)
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Failed to install {package}: {str(e)}")

    def uninstall_dependencies(self) -> None:
        """
        Uninstall dependency packages
        Args:
            -
        Returns:
            None
        """
        try:
            for package in self.installed_packages:
                try:
                    subprocess.check_call([sys.executable, "-m", "pip", "uninstall", "-y", package])
                except subprocess.CalledProcessError as e:
                    logger.warning(f"Failed to uninstall {package}: {str(e)}")
            self.installed_packages.clear()
        except Exception as e:
            logger.warning(f"Failed to uninstall dependencies: {repr(e)}")

    def reset(self,
              *,
              seed: int | None = None,
              options: Dict[str, str] | None = None) -> Tuple[AgentInput, dict[str, Any]]:
        """
        Reset the executor
        Args:
            seed: -
            options: -
        Returns:
            AgentInput, dict[str, Any]: -
        """
        self.close()
        self.local_namespace = {}
        self.global_namespace = {}
        self.step_finished = True
        self.installed_packages.clear()

    def finished(self) -> bool:
        """
        Check if the executor is finished
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
            self.uninstall_dependencies()
            sys.stdout = self.original_stdout
            self.output_buffer.close()
            self.local_namespace.clear()
            self.global_namespace.clear()
        except:
            pass
        finally:
            self.step_finished = True

    def step(
            self,
            actions: List[ActionModel],
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
                return (observation, reward,
                        kwargs.get("terminated",
                                   False), kwargs.get("truncated", False), {
                            "exception": "actions is empty"
                        })
            for action in actions:
                code = action.params.get("code", "")
                if not code:
                    continue
                try:
                    _, output, error = self.execute(code)
                    observation.content = output
                except Exception as e:
                    error = str(e)
                    output = error
                observation.action_result.append(
                    ActionResult(is_done=True,
                                 success=False if error else True,
                                 content=f"{output}",
                                 error=f"{error}",
                                 keep=False))
            reward = 1
        except Exception as e:
            fail_error = str(e)
        finally:
            self.step_finished = True

        return (observation, reward, kwargs.get("terminated", False),
                kwargs.get("truncated", False), {
                    "exception": fail_error
                })

    def execute(self, code: str) -> str:
        r"""Execute the given codes. Codes should be complete and runnable (like running a script), and need to explicitly use the print statement to get the output.

        Args:
            code (str): The input code to execute. Codes should be complete and runnable (like running a script), and need to explicitly use the print statement to get the output.

        Returns:
            str: The text output of the given codes.
        """
        from loguru import logger
        logger.debug(f"calling execute_code with code: {code}")
        output = self.interpreter.run(code, "python")
        # ruff: noqa: E501
        content = f"Executed the code below:\n```py\n{code}\n```\n> Executed Results:\n{output}"
        # print(content)
        return '', content, ''
    
    def get_execute_result(self):
        """
        Get the execute result
        Returns:
            output, error
        """
        output = None
        error = ''
        try:
            output = self.output_buffer.getvalue()
            self.output_buffer.truncate(0)
            self.output_buffer.seek(0)
            sys.stdout = self.original_stdout
        except Exception as e:
            error = f'{repr(e)}'
            logger.warning(f"Failed to get output, {repr(e)}")
        return output, error
