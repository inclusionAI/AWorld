# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import time
from typing import List, Dict, Any
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
            b: float = Field(description="Second addend")) -> Dict[str, Any]:
        result = a + b
        self.history.append(f"{a} + {b} = {result}")
        print(f"add:{a} + {b} = {result}")
        return {"result": result}

    def subtract(self,
                 a: float = Field(description="Minuend"),
                 b: float = Field(description="Subtrahend")) -> Dict[str, Any]:
        result = a - b
        self.history.append(f"{a} - {b} = {result}")
        print(f"subtract:{a} - {b} = {result}")
        return {"result": result}

    def multiply(self,
                 a: float = Field(description="First factor"),
                 b: float = Field(description="Second factor")) -> Dict[str, Any]:
        result = a * b
        self.history.append(f"{a} * {b} = {result}")
        print(f"multiply:{a} * {b} = {result}")
        return {"result": result}

    def divide(self,
               a: float = Field(description="Dividend"),
               b: float = Field(description="Divisor")
               ) -> Dict[str, Any]:
        if b == 0:
            raise ValueError("Divisor cannot be zero")
        result = a / b
        self.history.append(f"{a} / {b} = {result}")
        print(f"divide：{a} / {b} = {result}")
        return {"result": result}

    def get_history(self) -> Dict[str, List[str]]:
        """Get calculation history."""
        return {"history": self.history}

    def clear_history(self) -> Dict[str, str]:
        """Clear calculation history."""
        self.history = []
        return {"status": "History cleared"}


def main():
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
    main()
