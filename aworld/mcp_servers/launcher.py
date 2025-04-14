#!/usr/bin/env python
"""
MCP Servers Launcher

This module provides a unified entry point for launching all MCP servers.
It handles port allocation, process management, and graceful shutdown
of multiple MCP server instances.

Key features:
- Concurrent server launching
- Random port allocation
- Process lifecycle management
- Signal handling for graceful shutdown

Main functions:
- main: Entry point for launching all MCP servers
- MCPLauncher class: Manages the lifecycle of MCP server processes
"""

import argparse
import atexit
import os
import random
import signal
import subprocess
import sys
import time
from typing import Dict, List

from aworld.logs.util import logger

# Define the list of MCP servers to launch
MCP_SERVERS = [
    "arxiv_server.py",
    "audio_server.py",
    "code_server.py",
    "document_server.py",
    "download_server.py",
    "filesystem_server.py",
    "googlemaps_server.py",
    "image_server.py",
    "math_server.py",
    "reddit_server.py",
    "search_server.py",
    "sympy_server.py",
    "video_server.py",
]

# Special case for Playwright MCP
PLAYWRIGHT_PORT = 8931  # Default port for Playwright MCP


class MCPLauncher:
    """
    Manages the lifecycle of MCP server processes.
    """

    def __init__(
        self, servers: List[str], base_port: int = 2000, port_range: int = 8000
    ):
        """
        Initialize the MCP launcher.

        Args:
            servers: List of server script filenames to launch
            base_port: Starting port number for random allocation
            port_range: Range of ports to allocate from
        """
        self.servers = servers
        self.base_port = base_port
        self.port_range = port_range
        self.processes: Dict[str, subprocess.Popen] = {}
        self.ports: Dict[str, int] = {}

        # Register shutdown handler
        atexit.register(self.shutdown)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, sig, frame):
        """Handle termination signals by shutting down gracefully."""
        logger.info(f"\nReceived signal {sig}, shutting down...")
        self.shutdown()
        sys.exit(0)

    def _generate_ports(self) -> None:
        """Generate random unique ports for each server."""
        available_ports = list(range(self.base_port, self.base_port + self.port_range))
        random.shuffle(available_ports)

        for server in self.servers:
            self.ports[server] = available_ports.pop()

    def start(self, debug: bool = False) -> None:
        """
        Start all MCP servers with allocated ports.

        Args:
            debug: Whether to run in debug mode (more verbose output)
        """
        logger.info("Starting MCP servers...")

        # Generate random ports
        self._generate_ports()

        # Get the directory of this script
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # Start each Python server
        for server in self.servers:
            server_path = os.path.join(script_dir, server)
            port = self.ports[server]

            # Prepare environment with port
            env = os.environ.copy()
            env["MCP_PORT"] = str(port)

            # Start the process
            logger.info(f"Starting {server} on port {port}...")
            process = subprocess.Popen(
                [sys.executable, server_path],
                env=env,
                stdout=subprocess.PIPE if not debug else None,
                stderr=subprocess.PIPE if not debug else None,
                text=True,
                cwd=script_dir,
            )
            self.processes[server] = process

        # Start Playwright MCP
        logger.info(f"Starting Playwright MCP on port {PLAYWRIGHT_PORT}...")
        playwright_process = subprocess.Popen(
            f"yes | npx @playwright/mcp@latest --port {PLAYWRIGHT_PORT}",
            shell=True,
            stdout=subprocess.PIPE if not debug else None,
            stderr=subprocess.PIPE if not debug else None,
            text=True,
            cwd=script_dir,
        )
        self.processes["playwright"] = playwright_process

        logger.success("All MCP servers started successfully!")
        logger.success("Press Ctrl+C to stop all servers")

        # Keep the main process running
        try:
            while all(p.poll() is None for p in self.processes.values()):
                time.sleep(1)

            # Check if any process exited unexpectedly
            for server, process in self.processes.items():
                if process.poll() is not None:
                    logger.warning(
                        f"WARNING: {server} exited unexpectedly with code {process.returncode}"
                    )
        except KeyboardInterrupt:
            logger.info("\nShutting down...")

    def shutdown(self) -> None:
        """Gracefully shut down all running MCP server processes."""
        logger.info("Shutting down MCP servers...")

        for server, process in self.processes.items():
            if process.poll() is None:  # Process is still running
                logger.info(f"Stopping {server}...")
                try:
                    # Try to terminate gracefully first
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't respond
                    logger.warning(f"Force killing {server}...")
                    process.kill()

        logger.success("All MCP servers stopped")


def main():
    """Main entry point for the MCP launcher."""
    parser = argparse.ArgumentParser(
        description="Launch MCP servers with random port allocation"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Run in debug mode (show server output)"
    )
    args = parser.parse_args()

    launcher = MCPLauncher(MCP_SERVERS)
    launcher.start(debug=args.debug)


if __name__ == "__main__":
    main()
