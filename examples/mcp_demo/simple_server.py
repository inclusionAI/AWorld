# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""
Simple calculator MCP server example.
"""

import argparse
import os
import time
from typing import List, Dict, Any, Optional
from pydantic import Field

from aworld.mcp_client.decorator import mcp_server


@mcp_server(
    name="simple-calculator",
    mode="sse",
    host="127.0.0.1",
    port=8500,
    sse_path="/calculator/sse",
    auto_start=True  # if False you can start manually in main()
)
class Calculator:
    """Provides basic mathematical functions, including addition, subtraction, multiplication, division, and calculation history management."""

    def __init__(self):
        self.history = []

    def add(self,
            a: float = Field(description="First addend"),
            b: float = Field(description="Second addend")
            ) -> Dict[str, Any]:
        """
        Add two numbers

        :param a: First addend
        :param b: Second addend
        :return: Dictionary containing the result
        """
        result = a + b
        self.history.append(f"{a} + {b} = {result}")
        print(f"add:{a} + {b} = {result}")
        return {"result": result}

    def subtract(self,
                 a: float = Field(description="Minuend"),
                 b: float = Field(description="Subtrahend")
                 ) -> Dict[str, Any]:
        """
        Subtract the second number from the first number

        :param a: Minuend
        :param b: Subtrahend
        :return: Dictionary containing the result
        """
        result = a - b
        self.history.append(f"{a} - {b} = {result}")
        print(f"subtract:{a} - {b} = {result}")
        return {"result": result}

    def multiply(self,
                 a: float = Field(description="First factor"),
                 b: float = Field(description="Second factor")
                 ) -> Dict[str, Any]:
        """
        Multiply two numbers

        :param a: First factor
        :param b: Second factor
        :return: Dictionary containing the result
        """
        result = a * b
        self.history.append(f"{a} * {b} = {result}")
        print(f"multiply:{a} * {b} = {result}")
        return {"result": result}

    def divide(self,
               a: float = Field(description="Dividend"),
               b: float = Field(description="Divisor")
               ) -> Dict[str, Any]:
        """
        Divide the first number by the second number

        :param a: Dividend
        :param b: Divisor
        :return: Dictionary containing the result
        """
        if b == 0:
            raise ValueError("Divisor cannot be zero")
        result = a / b
        self.history.append(f"{a} / {b} = {result}")
        print(f"divide：{a} / {b} = {result}")
        return {"result": result}

    def get_history(self) -> Dict[str, List[str]]:
        """
        Get calculation history

        :return: Dictionary containing the history
        """
        return {"history": self.history}

    def clear_history(self) -> Dict[str, str]:
        """
        Clear calculation history

        :return: Dictionary containing operation status
        """
        self.history = []
        return {"status": "History cleared"}


@mcp_server(
    name="weather",
    mode="sse",
    host="127.0.0.1",
    port=8200,
    sse_path="/weather/sse",
    auto_start=False  # Don't auto-start, we'll start manually in main()
)
class WeatherService:
    """A service that can query and manage city weather information, supports adding cities and getting city weather data."""

    def __init__(self):
        self.locations = {
            "Beijing": {"temp": 20, "humidity": 60, "weather": "Sunny"},
            "Shanghai": {"temp": 25, "humidity": 70, "weather": "Cloudy"},
            "Guangzhou": {"temp": 30, "humidity": 80, "weather": "Rainy"}
        }

    def get_current_weather(self,
                            location: str = Field(description="City name")
                            ) -> Dict[str, Any]:
        """
        Get current weather for a specified city

        :param location: City name
        :return: Dictionary containing weather information
        """
        if location not in self.locations:
            return {"error": f"City {location} does not exist"}
        return {"weather": self.locations[location]}

    def get_locations(self) -> Dict[str, List[str]]:
        """
        Get list of all available cities

        :return: Dictionary containing city list
        """
        return {"locations": list(self.locations.keys())}

    def add_location(self,
                     location: str = Field(description="City name"),
                     temp: float = Field(description="Temperature (Celsius)"),
                     humidity: float = Field(description="Humidity (percentage)"),
                     weather: str = Field(description="Weather description")
                     ) -> Dict[str, str]:
        """
        Add or update weather information for a city

        :param location: City name
        :param temp: Temperature (Celsius)
        :param humidity: Humidity (percentage)
        :param weather: Weather description
        :return: Dictionary containing operation status
        """
        self.locations[location] = {
            "temp": temp,
            "humidity": humidity,
            "weather": weather
        }
        return {"status": f"Weather information for {location} has been updated"}


@mcp_server(
    name="async-calculator",
    mode="sse",
    host="127.0.0.1",
    port=8200,
    sse_path="/async-calculator/sse",
    auto_start=False  # Don't auto-start, we'll start manually in main()
)
class AsyncCalculator:
    """Provides asynchronous version of basic mathematical functions, including addition, subtraction, multiplication, division, and calculation history management."""

    def __init__(self):
        self.history = []

    async def add(self,
                  a: float = Field(description="First addend"),
                  b: float = Field(description="Second addend")
                  ) -> Dict[str, Any]:
        """
        Add two numbers (async version)

        :param a: First addend
        :param b: Second addend
        :return: Dictionary containing the result
        """
        result = a + b
        self.history.append(f"{a} + {b} = {result}")
        print(f"async_add:{a} + {b} = {result}")
        return {"result": result}

    async def subtract(self,
                       a: float = Field(description="Minuend"),
                       b: float = Field(description="Subtrahend")
                       ) -> Dict[str, Any]:
        """
        Subtract the second number from the first number (async version)

        :param a: Minuend
        :param b: Subtrahend
        :return: Dictionary containing the result
        """
        result = a - b
        self.history.append(f"{a} - {b} = {result}")
        print(f"async_subtract:{a} - {b} = {result}")
        return {"result": result}

    async def multiply(self,
                       a: float = Field(description="First factor"),
                       b: float = Field(description="Second factor")
                       ) -> Dict[str, Any]:
        """
        Multiply two numbers (async version)

        :param a: First factor
        :param b: Second factor
        :return: Dictionary containing the result
        """
        result = a * b
        self.history.append(f"{a} * {b} = {result}")
        print(f"async_multiply:{a} * {b} = {result}")
        return {"result": result}

    async def divide(self,
                     a: float = Field(description="Dividend"),
                     b: float = Field(description="Divisor")
                     ) -> Dict[str, Any]:
        """
        Divide the first number by the second number (async version)

        :param a: Dividend
        :param b: Divisor
        :return: Dictionary containing the result
        """
        if b == 0:
            raise ValueError("Divisor cannot be zero")
        result = a / b
        self.history.append(f"{a} / {b} = {result}")
        print(f"async_divide：{a} / {b} = {result}")
        return {"result": result}

    async def get_history(self) -> Dict[str, List[str]]:
        """
        Get calculation history (async version)

        :return: Dictionary containing the history
        """
        return {"history": self.history}

    async def clear_history(self) -> Dict[str, str]:
        """
        Clear calculation history (async version)

        :return: Dictionary containing operation status
        """
        self.history = []
        return {"status": "History cleared"}


def main():
    parser = argparse.ArgumentParser(description="MCP Simple Calculator Server")
    parser.add_argument("--server-type", choices=["calculator", "weather", "async-calculator"], default="calculator",
                        help="Server type, options: 'calculator', 'weather', or 'async-calculator'")
    parser.add_argument("--mode", choices=["stdio", "sse"], default="sse",
                        help="Server running mode, options: 'stdio' or 'sse'")
    parser.add_argument("--host", default="127.0.0.1", help="Server host address, default is 127.0.0.1")
    parser.add_argument("--port", type=int, default=8200, help="Server port number, default is 8200")
    parser.add_argument("--sse-path", default=None, help="SSE path, defaults based on server type")

    args = parser.parse_args()

    # Read configuration from environment variables if set
    server_type = os.environ.get("MCP_SERVER_TYPE", args.server_type)
    mode = os.environ.get("MCP_MODE", args.mode)
    host = os.environ.get("MCP_HOST", args.host)

    # Handle integer type
    try:
        port = int(os.environ.get("MCP_PORT", args.port))
    except (ValueError, TypeError):
        port = args.port

    # Create server instance based on type
    if server_type == "calculator":
        server = Calculator()
        default_sse_path = "/calculator/sse"
    elif server_type == "async-calculator":
        server = AsyncCalculator()
        default_sse_path = "/async-calculator/sse"
    else:
        server = WeatherService()
        default_sse_path = "/weather/sse"

    # Set SSE path from args, env, or default
    sse_path = os.environ.get("MCP_SSE_PATH", args.sse_path or default_sse_path)

    print(f"Using configuration: server_type={server_type}, mode={mode}, host={host}, port={port}, sse_path={sse_path}")

    # Run server with provided configuration
    server.run(mode=mode, host=host, port=port, sse_path=sse_path)


def auto_start_example():

    Calculator()

    print("Auto-starting calculator has been initialized.")
    print("Server is running in background. Press Ctrl+C to exit.")

    try:
        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Exiting...")


if __name__ == "__main__":
    auto_start_example()