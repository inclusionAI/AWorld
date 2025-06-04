import logging
import sys
from typing import List

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# pylint: disable=W0707
# Initialize MCP server for CSV operations
mcp = FastMCP("txt-server")

# Placeholder for logger if aworld logger is not used
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# Load environment variables
load_dotenv()


# Pydantic models for tool outputs
class TxTCountResult(BaseModel):
    """Model for CSV file metadata."""

    file_path: str
    target_text: str
    total_line_count: int
    line_number_contains_text: List[int]
    error: bool = False


@mcp.tool(
    description="Find target text in which line of a text file. Report line number contains target text.",
)
async def find_text_in_which_line(
    file_path: str = Field(description="The absolute path to the CSV file (.csv)."),
    target_text: str = Field(description="The target text to find."),
) -> TxTCountResult:
    """
    Find target text in which line of a text file. Report line number contains target text."
    """
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            lines = file.readlines()
            line_number_contains_text = [i + 1 for i, line in enumerate(lines) if target_text in line]
            total_line_count = len(lines)
            result = TxTCountResult(
                file_path=file_path,
                target_text=target_text,
                total_line_count=total_line_count,
                line_number_contains_text=line_number_contains_text,
                error=False,
            )
        return result
    except Exception as e:
        logger.error(f"Error finding text in which line of {file_path}: {e}")
        return TxTCountResult(
            file_path=file_path,
            target_text=target_text,
            total_line_count=0,
            line_number_contains_text=[],
            error=True,
        )


def main():
    """
    Main function to start the MCP server.
    """
    load_dotenv()  # Load environment variables
    mcp.run(transport="stdio")  # Use FastMCP's run method


# Make the module callable for uvx (Reference audio_server.py)
def __call__():
    """
    Make the module callable for uvx.
    This function is called when the module is executed directly.
    """
    main()


# Add this for compatibility with uvx
if __name__ != "__main__":  # Ensure this is also set when imported as a module
    sys.modules[__name__].__call__ = __call__

# Run the server when the script is executed directly
if __name__ == "__main__":
    main()
