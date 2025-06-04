import unittest
from unittest.mock import patch, MagicMock
import os

from examples.tools.common import Tools

from aworld.core.tool.base import ToolFactory


class TestShellTool(unittest.TestCase):
    def setUp(self):
        self.shell_tool = ToolFactory(Tools.SHELL.value)

    def test_init(self):
        """Test initialization"""
        self.assertEqual(self.shell_tool.type, "function")
        self.assertEqual(self.shell_tool.working_dir, "/tmp")
        self.assertIn("TEST_ENV", self.shell_tool.env)
        self.assertEqual(self.shell_tool.env["TEST_ENV"], "test")
        self.assertEqual(self.shell_tool.processes, [])
        self.assertFalse(self.shell_tool.finished)

    def test_name(self):
        """Test name method"""
        self.assertEqual(self.shell_tool.name(), "shell")

    def test_reset(self):
        """Test reset method"""
        observation, info = self.shell_tool.reset()
        self.assertIsNone(self.shell_tool.working_dir)
        self.assertEqual(self.shell_tool.env, os.environ.copy())
        self.assertEqual(self.shell_tool.processes, [])
        self.assertFalse(self.shell_tool.finished)
        self.assertEqual(info, {})

    def test_finished(self):
        """Test finished method"""
        self.assertFalse(self.shell_tool.finished)
        self.shell_tool._finished = False
        self.assertFalse(self.shell_tool.finished)

    @patch('subprocess.run')
    def test_execute(self, mock_run):
        """Test execute method"""
        # Setup mock
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.stdout = "test output"
        mock_process.stderr = ""
        mock_run.return_value = mock_process

        # Test successful execution
        result = self.shell_tool.execute("ls -l")
        self.assertTrue(result['success'])
        self.assertEqual(result['return_code'], 0)
        self.assertEqual(result['stdout'], "test output")
        self.assertEqual(result['stderr'], "")
        self.assertEqual(result['script'], "ls -l")

        # Test execution with error
        mock_process.returncode = 1
        result = self.shell_tool.execute("invalid_command")
        self.assertFalse(result['success'])

    @patch('subprocess.Popen')
    def test_execute_async(self, mock_popen):
        """Test execute_async method"""
        mock_process = MagicMock()
        mock_popen.return_value = mock_process

        process = self.shell_tool.execute_async("long_running_command")
        self.assertEqual(process, mock_process)
        self.assertIn(mock_process, self.shell_tool.processes)

    def test_close(self):
        """Test close method"""
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        self.shell_tool.processes = [mock_process]

        self.shell_tool.close()
        self.assertEqual(self.shell_tool.processes, [])
        self.assertTrue(self.shell_tool.finished)
