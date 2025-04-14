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
import threading
from typing import Callable, Dict, List

from aworld.logs.util import logger
from aworld.mcp_servers.arxiv_server import mcpdownloadarxivpaper, mcpsearcharxivpaper
from aworld.mcp_servers.audio_server import mcptranscribeaudio
from aworld.mcp_servers.code_server import mcpexecutecode, mcpgeneratecode
from aworld.mcp_servers.document_server import (
    mcpreaddocx,
    mcpreadexcel,
    mcpreadjson,
    mcpreadpdf,
    mcpreadpptx,
    mcpreadsourcecode,
    mcpreadtext,
    mcpreadxml,
)
from aworld.mcp_servers.download_server import mcpdownloadfiles
from aworld.mcp_servers.filesystem_server import (
    mcpchangepermissions,
    mcpcheckpath,
    mcpcompressfiles,
    mcpcopypath,
    mcpcreatedirectory,
    mcpdeletepath,
    mcpextractarchive,
    mcpfindduplicates,
    mcpgetdiskusage,
    mcpgetfileinfo,
    mcplistdir,
    mcpmovepath,
    mcpreadfile,
    mcpsearchfiles,
    mcpwritefile,
)
from aworld.mcp_servers.googlemaps_server import (
    mcpdirections,
    mcpdistancematrix,
    mcpelevation,
    mcpgeocode,
    mcpgetlatlng,
    mcpgetpostcode,
    mcpplacedetails,
    mcpplacesearch,
    mcptimezone,
)
from aworld.mcp_servers.image_server import mcpreasoningimage
from aworld.mcp_servers.math_server import (
    mcpbasicmath,
    mcpconversion,
    mcpgeometry,
    mcprandom,
    mcpsolveequation,
    mcpstatistics,
    mcptrigonometry,
)
from aworld.mcp_servers.reddit_server import (
    mcpgethotposts,
    mcpgetpostcomments,
    mcpgetsubredditinfo,
    mcpgettopsubreddits,
    mcpgetuserinfo,
    mcpgetuserposts,
    mcpsearchreddit,
)
from aworld.mcp_servers.search_server import (
    mcpsearchduckduckgo,
    mcpsearchexa,
    mcpsearchgoogle,
)
from aworld.mcp_servers.sympy_server import (
    mcpalgebraic,
    mcpcalculus,
    mcpmatrix,
    mcpsolve,
    mcpsolvelinear,
    mcpsolveode,
)
from aworld.mcp_servers.utils import run_mcp_server
from aworld.mcp_servers.video_server import (
    mcpanalyzevideo,
    mcpextractvideosubtitles,
    mcpsummarizevideo,
)

# Special case for Playwright MCP
PLAYWRIGHT_PORT: int = 8931  # Default port for Playwright MCP

# Create mapping from server names to functions
SERVER_FUNCTIONS: Dict[str, List[Callable]] = {
    "arxiv": [mcpdownloadarxivpaper, mcpsearcharxivpaper],
    "audio": [mcptranscribeaudio],
    "code": [mcpexecutecode, mcpgeneratecode],
    "document": [
        mcpreaddocx,
        mcpreadexcel,
        mcpreadjson,
        mcpreadpdf,
        mcpreadpptx,
        mcpreadsourcecode,
        mcpreadtext,
        mcpreadxml,
    ],
    "download": [mcpdownloadfiles],
    "filesystem": [
        mcpchangepermissions,
        mcpcheckpath,
        mcpcompressfiles,
        mcpcopypath,
        mcpcreatedirectory,
        mcpdeletepath,
        mcpextractarchive,
        mcpfindduplicates,
        mcpgetdiskusage,
        mcpgetfileinfo,
        mcplistdir,
        mcpmovepath,
        mcpreadfile,
        mcpsearchfiles,
        mcpwritefile,
    ],
    "googlemaps": [
        mcpdirections,
        mcpdistancematrix,
        mcpelevation,
        mcpgeocode,
        mcpgetlatlng,
        mcpgetpostcode,
        mcpplacedetails,
        mcpplacesearch,
        mcptimezone,
    ],
    "image": [mcpreasoningimage],
    "math": [
        mcpbasicmath,
        mcpconversion,
        mcpgeometry,
        mcprandom,
        mcpsolveequation,
        mcpstatistics,
        mcptrigonometry,
    ],
    "reddit": [
        mcpgethotposts,
        mcpgetpostcomments,
        mcpgetsubredditinfo,
        mcpgettopsubreddits,
        mcpgetuserinfo,
        mcpgetuserposts,
        mcpsearchreddit,
    ],
    "search": [mcpsearchduckduckgo, mcpsearchexa, mcpsearchgoogle],
    "sympy": [
        mcpalgebraic,
        mcpcalculus,
        mcpmatrix,
        mcpsolve,
        mcpsolvelinear,
        mcpsolveode,
    ],
    "video": [
        mcpanalyzevideo,
        mcpextractvideosubtitles,
        mcpsummarizevideo,
    ],
}


class MCPLauncher:
    """
    Manages the lifecycle of MCP server processes.
    """

    def __init__(
        self,
        servers: Dict[str, List[Callable]],
        base_port: int = 2000,
        port_range: int = 8000,
    ):
        """
        Initialize the MCP launcher.

        Args:
            servers: Dictionary of server-tools to launch
            base_port: Starting port number for random allocation
            port_range: Range of ports to allocate from
        """
        self.servers = servers
        self.base_port = base_port
        self.port_range = port_range
        self.processes: Dict[str, subprocess.Popen] = {}
        self.threads: Dict[str, threading.Thread] = {}
        self.ports: Dict[str, int] = {}

        # Register shutdown handler
        atexit.register(self.shutdown)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, sig, frame):
        """Handle termination signals by shutting down gracefully."""
        logger.info(f"\nReceived signal {sig}, shutting down...")
        sys.exit(0)

    def _generate_ports(self) -> None:
        """Generate random unique ports for each server."""
        available_ports = list(range(self.base_port, self.base_port + self.port_range))
        random.shuffle(available_ports)

        for server, _ in self.servers.items():
            self.ports[server] = available_ports.pop()

    def start(self) -> None:
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

        # Start each server
        for server_name, funcs in self.servers.items():
            port = self.ports[server_name]

            if not funcs:
                logger.warning(f"No functions found for {server_name}, skipping...")
                continue

            # Use threads to start servers to avoid blocking the main thread
            logger.info(f"Starting {server_name} on port {port}")
            thread = threading.Thread(
                target=run_mcp_server,
                args=(server_name, funcs, port),
                daemon=True,
            )
            thread.start()

            # Store threads instead of processes
            self.threads[server_name] = thread

        # Start Playwright MCP
        # logger.info(f"Starting Playwright MCP on port {PLAYWRIGHT_PORT}...")
        # playwright_process = subprocess.Popen(
        #     f"yes | npx @playwright/mcp@latest --port {PLAYWRIGHT_PORT}",
        #     shell=True,
        #     stdout=subprocess.PIPE if not debug else None,
        #     stderr=subprocess.PIPE if not debug else None,
        #     text=True,
        #     cwd=script_dir,
        # )
        # self.processes["playwright"] = playwright_process

        logger.success("All MCP servers started successfully!")
        logger.success("Press Ctrl+C to stop all servers")

        while True:
            try:
                pass
            except KeyboardInterrupt as e:
                logger.info("\nShutting down...")
                sys.exit(0)

        # Keep the main process running
        # try:
        #     # For threads, we can't use poll(), so we only check the playwright process
        #     while playwright_process.poll() is None:
        #         time.sleep(1)

        #     # If playwright process exits, log a warning
        #     if playwright_process.poll() is not None:
        #         logger.warning(
        #             f"WARNING: playwright exited unexpectedly with code {playwright_process.returncode}"
        #         )
        # except KeyboardInterrupt:
        #     logger.info("\nShutting down...")

    def shutdown(self) -> None:
        """Gracefully shut down all running MCP server processes."""
        logger.info("Shutting down MCP servers...")

        # Shutdown playwright process
        if "playwright" in self.processes:
            process = self.processes["playwright"]
            if (
                hasattr(process, "poll") and process.poll() is None
            ):  # Is a process and still running
                logger.info("Stopping playwright...")
                try:
                    # Try to terminate gracefully first
                    process.terminate()
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # Force kill if it doesn't respond
                    logger.warning("Force killing playwright...")
                    process.kill()

        # Threads are daemon threads and will terminate with the main program
        # We just need to log
        for server in [s for s in self.threads if s != "playwright"]:
            logger.info(f"Stopping {server}...")

        logger.success("All MCP servers stopped")


def main():
    """Main entry point for the MCP launcher."""
    parser = argparse.ArgumentParser(
        description="Launch MCP servers with random port allocation"
    )
    parser.add_argument(
        "--server_name",
        type=str,
        help=f"Available servers: {', '.join(SERVER_FUNCTIONS.keys())}",
        choices=SERVER_FUNCTIONS.keys(),
        default=None,
    )
    args = parser.parse_args()

    if args.server_name:
        launcher = MCPLauncher({args.server_name: SERVER_FUNCTIONS[args.server_name]})
        logger.success(f"Launching server: {args.server_name}")
    else:
        launcher = MCPLauncher(SERVER_FUNCTIONS)
        logger.success("Launching all servers")

    launcher.start()


if __name__ == "__main__":
    main()
