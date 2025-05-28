import logging
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Union

import chardet
import pandas as pd
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from tabulate import tabulate  # For creating markdown tables

# pylint: disable=W0707
# Initialize MCP server for CSV operations
mcp = FastMCP("csv-server")

# Placeholder for logger if aworld logger is not used
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# Load environment variables
load_dotenv()


# Pydantic models for tool outputs
class CsvMetadata(BaseModel):
    """Model for CSV file metadata."""

    file_path: str
    file_name: str
    row_count: int
    column_count: int
    headers: List[str]
    error: Optional[str] = None


class CsvData(BaseModel):
    """Model for extracted CSV data."""

    file_path: str
    data: Union[
        List[Dict[str, Any]], List[List[Any]], str
    ]  # Data can be list of dicts, list of lists, or markdown string
    row_count_read: int  # Actual number of rows read
    column_count_read: int  # Actual number of columns read
    total_row_count: Optional[int] = None  # Total rows in the CSV
    total_column_count: Optional[int] = None  # Total columns in the CSV
    error: Optional[str] = None


class CsvContent(BaseModel):
    """Model for raw CSV content."""

    file_path: str
    csv_data: str
    error: Optional[str] = None


class MarkdownTableContent(BaseModel):
    """Model for Markdown table content."""

    file_path: str
    markdown_table: str
    error: Optional[str] = None


def check_file_readable(file_path: str) -> Optional[str]:
    """Checks if the given file exists, is readable, and has a .csv extension."""
    if not os.path.exists(file_path):
        return f"File does not exist: {file_path}"
    if not os.access(file_path, os.R_OK):
        return f"File is not readable: {file_path}"
    if not file_path.lower().endswith(".csv"):
        return f"Unsupported file format. Only .csv files are supported: {file_path}"
    return None


def read_csv_file(file_path: str) -> pd.DataFrame:
    """Helper function to read a CSV file into a pandas DataFrame."""
    try:
        df = pd.read_csv(file_path)
        return df
    except Exception:
        with open(file_path, "rb") as f:
            raw_data = f.read()
            result = chardet.detect(raw_data)
            encoding = result["encoding"]
        with open(file_path, "r", encoding=encoding) as file:
            first = True
            data, index_map = {}, {}
            for line in file.readlines():
                if first:
                    first = False
                    headers = line.strip().split(",")
                    data = {header: [] for header in headers}
                    index_map = dict(enumerate(headers))
                else:
                    values = line.strip().split(",")
                    if len(values) == len(headers):
                        for i, value in enumerate(values):
                            data[index_map[i]].append(value)
                    else:
                        continue
            return pd.DataFrame.from_dict(data)


@mcp.tool(description="Retrieves metadata from a CSV file (e.g., row count, column count, headers).")
async def get_csv_metadata(
    file_path: str = Field(description="The absolute path to the CSV file (.csv)."),
) -> Dict[str, Any]:
    """
    Retrieves metadata from a CSV file.
    """
    error = check_file_readable(file_path)
    if error:
        logger.error(error)
        raise FileNotFoundError(error)
    try:
        df = read_csv_file(file_path)
        row_count = df.shape[0]
        column_count = df.shape[1]
        headers = df.columns.tolist()
        metadata = CsvMetadata(
            file_path=file_path,
            file_name=os.path.basename(file_path),
            row_count=row_count,
            column_count=column_count,
            headers=headers,
        )
        return metadata.model_dump()
    except Exception as e:
        logger.error(f"Error getting metadata from {file_path}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error processing file for metadata: {str(e)}")


@mcp.tool(
    description="Reads data from a CSV file. "
    "Supports reading specific ranges, limiting rows/columns, and different output formats."
)
async def read_csv_data(
    file_path: str = Field(description="The absolute path to the CSV file (.csv)."),
    return_format: str = Field(
        default="list_of_dicts",
        description="Optional. Format of the returned data: 'list_of_dicts', 'list_of_lists', or 'markdown_table'.",
        enum=["list_of_dicts", "list_of_lists", "markdown_table"],
    ),
) -> Union[List[Dict[str, Any]], List[List[Any]], str]:
    """
    Reads data from a CSV file.
    """
    error = check_file_readable(file_path)
    if error:
        logger.error(error)
        raise FileNotFoundError(error)
    try:
        df = read_csv_file(file_path)
        row_count_read = df.shape[0]
        column_count_read = df.shape[1]

        total_row_count = None
        total_column_count = None
        try:
            full_df = read_csv_file(file_path)
            total_row_count = full_df.shape[0]
            total_column_count = full_df.shape[1]
        except Exception:
            pass  # Ignore errors if full read fails

        if return_format == "list_of_dicts":
            data = df.to_dict(orient="records")
        elif return_format == "list_of_lists":
            data = [df.columns.tolist()] + df.values.tolist()
        elif return_format == "markdown_table":
            data = tabulate(df, headers="keys", tablefmt="pipe")
        else:
            raise ValueError(f"Invalid return_format: {return_format}")

        result = CsvData(
            file_path=file_path,
            data=data,
            row_count_read=row_count_read,
            column_count_read=column_count_read,
            total_row_count=total_row_count,
            total_column_count=total_column_count,
        )
        return result.data
    except Exception as e:
        logger.error(f"Error reading CSV data from {file_path}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error processing file for reading CSV data: {str(e)}")


@mcp.tool(description="Converts data from a specific range in a CSV file to Markdown table format (string).")
async def convert_csv_to_markdown(
    file_path: str = Field(description="The absolute path to the CSV file (.csv)."),
) -> str:
    """
    Converts data from a specific range in a CSV file to Markdown table format string.
    """
    error = check_file_readable(file_path)
    if error:
        logger.error(error)
        raise FileNotFoundError(error)
    try:
        df = read_csv_file(file_path)
        markdown_table = tabulate(df, headers="keys", tablefmt="pipe")
        result = MarkdownTableContent(file_path=file_path, markdown_table=markdown_table)
        return result.markdown_table
    except Exception as e:
        logger.error(f"Error converting CSV data from {file_path} to Markdown: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error processing file for Markdown conversion: {str(e)}")


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
