import logging
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Union

import pandas as pd
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from tabulate import tabulate  # For creating markdown tables

# Import libraries for older .xls files if needed, though pandas often handles this
try:
    import xlrd
except ImportError:
    logging.warning("xlrd library is not installed. Processing of older .xls files might be limited.")
    xlrd = None

# Use aworld logger (if a unified logger exists in the project)
# from aworld.logs.util import logger # Uncomment if aworld logger is available

# Initialize MCP server
mcp = FastMCP("excel-server")

# Placeholder for logger if aworld logger is not used
logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# pylint: disable=W0707
# Load environment variables
load_dotenv()


# Pydantic models for tool outputs
class ExcelMetadata(BaseModel):
    """Model for Excel file metadata."""

    file_path: str
    file_name: str
    sheet_count: int
    sheet_names: List[str]
    error: Optional[str] = None


class SheetDimensions(BaseModel):
    """Model for sheet dimensions."""

    file_path: str
    sheet_name: str
    row_count: int
    column_count: int
    error: Optional[str] = None


class SheetData(BaseModel):
    """Model for extracted sheet data."""

    file_path: str
    sheet_name: str
    data: Union[
        List[Dict[str, Any]], List[List[Any]], str
    ]  # Data can be list of dicts, list of lists, or markdown string
    row_count_read: int  # Actual number of rows read
    column_count_read: int  # Actual number of columns read
    total_row_count: Optional[int] = None  # Total rows in the sheet
    total_column_count: Optional[int] = None  # Total columns in the sheet
    error: Optional[str] = None


class CsvContent(BaseModel):
    """Model for CSV content."""

    file_path: str
    sheet_name: str
    csv_data: str
    error: Optional[str] = None


class MarkdownTableContent(BaseModel):
    """Model for Markdown table content."""

    file_path: str
    sheet_name: str
    markdown_table: str
    error: Optional[str] = None


def check_file_readable(file_path: str) -> Optional[str]:
    """Check if file exists and is readable, return error message or None."""
    if not os.path.exists(file_path):
        return f"File does not exist: {file_path}"
    if not os.access(file_path, os.R_OK):
        return f"File is not readable: {file_path}"
    if not (file_path.lower().endswith(".xlsx") or file_path.lower().endswith(".xls")):
        return f"Unsupported file format. Only .xlsx and .xls are supported: {file_path}"
    return None


def read_excel_file(file_path: str) -> pd.ExcelFile:
    """Helper function to read Excel file using pandas, handling .xls if xlrd is installed."""
    if file_path.lower().endswith(".xls") and xlrd is None:
        raise RuntimeError("xlrd library is required to read .xls files but not found.")
    return pd.ExcelFile(file_path)


@mcp.tool(description="Retrieves metadata from an Excel file (e.g., sheet names, number of sheets).")
async def get_excel_metadata(
    file_path: str = Field(description="The absolute path to the Excel file (.xlsx or .xls)."),
) -> Dict[str, Any]:
    """
    Retrieves metadata from an Excel file.

    Args:
        file_path: The absolute path to the Excel file.

    Returns:
        A dictionary containing metadata such as sheet names and number of sheets.

    Raises:
        FileNotFoundError: If the specified file_path does not exist.
        RuntimeError: If required libraries are not installed or an error occurs.
        ValueError: If unsupported file format.
    """
    error = check_file_readable(file_path)
    if error:
        logger.error(error)
        raise FileNotFoundError(error)

    try:
        xls = read_excel_file(file_path)
        sheet_names = xls.sheet_names
        sheet_count = len(sheet_names)

        metadata = ExcelMetadata(
            file_path=file_path, file_name=os.path.basename(file_path), sheet_count=sheet_count, sheet_names=sheet_names
        )
        return metadata.model_dump()

    except Exception as e:
        logger.error(f"Error getting metadata from {file_path}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error processing Excel for metadata: {str(e)}")


@mcp.tool(description="Retrieves the number of rows and columns for a specific sheet in an Excel file.")
async def get_sheet_dimensions(
    file_path: str = Field(description="The absolute path to the Excel file (.xlsx or .xls)."),
    sheet_name: str = Field(description="The name of the sheet."),
) -> Dict[str, int]:
    """
    Retrieves the number of rows and columns for a specific sheet.

    Args:
        file_path: The absolute path to the Excel file.
        sheet_name: The name of the sheet.

    Returns:
        A dictionary containing 'row_count' and 'column_count'.

    Raises:
        FileNotFoundError: If the specified file_path does not exist.
        ValueError: If the sheet name is not found or file format is unsupported.
        RuntimeError: If required libraries are not installed or an error occurs.
    """
    error = check_file_readable(file_path)
    if error:
        logger.error(error)
        raise FileNotFoundError(error)

    try:
        xls = read_excel_file(file_path)
        if sheet_name not in xls.sheet_names:
            raise ValueError(
                f"Sheet '{sheet_name}' not found in file '{file_path}'. Available sheets: {xls.sheet_names}"
            )

        df = xls.parse(sheet_name)
        row_count = df.shape[0]
        column_count = df.shape[1]

        dimensions = SheetDimensions(
            file_path=file_path, sheet_name=sheet_name, row_count=row_count, column_count=column_count
        )
        return dimensions.model_dump()

    except Exception as e:
        logger.error(f"Error getting dimensions for sheet '{sheet_name}' in {file_path}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error processing Excel for sheet dimensions: {str(e)}")


@mcp.tool(
    description="Reads data from a specific sheet in an Excel file. "
    "Supports reading specific ranges, limiting rows/columns, and different output formats."
)
async def read_excel_sheet(
    file_path: str = Field(description="The absolute path to the Excel file (.xlsx or .xls)."),
    sheet_name: str = Field(description="The name of the sheet."),
    start_row: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed starting row to read from (inclusive).",
    ),
    end_row: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed ending row to read up to (inclusive).",
    ),
    start_column: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed starting column to read from (inclusive).",
    ),
    end_column: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed ending column to read up to (inclusive).",
    ),
    max_rows: Optional[int] = Field(
        default=None,
        description="Optional. Maximum number of rows to read from the starting row. "
        "Applied after start_row/end_row if specified.",
    ),
    max_columns: Optional[int] = Field(
        default=None,
        description="Optional. Maximum number of columns to read from the starting column. "
        "Applied after start_column/end_column if specified.",
    ),
    return_format: str = Field(
        default="list_of_dicts",
        description="Optional. Format of the returned data: 'list_of_dicts', 'list_of_lists', or 'markdown_table'.",
        enum=["list_of_dicts", "list_of_lists", "markdown_table"],
    ),
) -> Union[List[Dict[str, Any]], List[List[Any]], str]:
    """
    Reads data from a specific sheet in an Excel file.

    Args:
        file_path: The absolute path to the Excel file.
        sheet_name: The name of the sheet.
        start_row: Optional. The 0-indexed starting row (inclusive).
        end_row: Optional. The 0-indexed ending row (inclusive).
        start_column: Optional. The 0-indexed starting column (inclusive).
        end_column: Optional. The 0-indexed ending column (inclusive).
        max_rows: Optional. Maximum number of rows to read.
        max_columns: Optional. Maximum number of columns to read.
        return_format: Optional. 'list_of_dicts', 'list_of_lists', or 'markdown_table'.

    Returns:
        Data from the sheet in the specified format.

    Raises:
        FileNotFoundError: If the specified file_path does not exist.
        ValueError: If the sheet name is not found, file format is unsupported, or invalid parameters.
        RuntimeError: If required libraries are not installed or an error occurs.
    """
    error = check_file_readable(file_path)
    if error:
        logger.error(error)
        raise FileNotFoundError(error)

    try:
        xls = read_excel_file(file_path)
        if sheet_name not in xls.sheet_names:
            raise ValueError(
                f"Sheet '{sheet_name}' not found in file '{file_path}'. Available sheets: {xls.sheet_names}"
            )

        # Determine rows to read based on start_row, end_row, and max_rows
        skiprows = start_row if start_row is not None else 0
        nrows = None
        if end_row is not None:
            nrows = end_row - skiprows + 1
        if max_rows is not None:
            if nrows is None:
                nrows = max_rows
            else:
                nrows = min(nrows, max_rows)

        # Determine columns to read based on start_column, end_column, and max_columns
        usecols = None
        if start_column is not None or end_column is not None or max_columns is not None:
            col_start = start_column if start_column is not None else 0
            col_end = end_column if end_column is not None else float("inf")
            col_limit = max_columns if max_columns is not None else float("inf")

            # Calculate the actual range of columns to read
            actual_col_end = min(col_end, col_start + col_limit - 1) if col_limit is not float("inf") else col_end
            if actual_col_end < col_start:
                usecols = []  # No columns to read
            else:
                usecols = list(range(col_start, int(actual_col_end) + 1))

        # Read the sheet into a pandas DataFrame
        # Use header=None if you want to include the first row as data, not header
        # For simplicity, assuming the first row is header unless start_row > 0
        header = 0 if skiprows == 0 else None
        df = xls.parse(sheet_name, skiprows=skiprows, nrows=nrows, usecols=usecols, header=header)

        row_count_read = df.shape[0]
        column_count_read = df.shape[1]

        # Get total dimensions for context if needed
        total_row_count = None
        total_column_count = None
        try:
            full_df_shape = xls.parse(sheet_name).shape
            total_row_count = full_df_shape[0]
            total_column_count = full_df_shape[1]
        except Exception:
            # Ignore errors if getting full shape fails for very large files
            pass

        # Convert DataFrame to the requested format
        if return_format == "list_of_dicts":
            data = df.to_dict(orient="records")
        elif return_format == "list_of_lists":
            # Include header row in list of lists
            data = [df.columns.tolist()] + df.values.tolist()
        elif return_format == "markdown_table":
            # Convert DataFrame to Markdown table string
            data = tabulate(df, headers="keys", tablefmt="pipe")
        else:
            raise ValueError(f"Invalid return_format: {return_format}")

        result = SheetData(
            file_path=file_path,
            sheet_name=sheet_name,
            data=data,
            row_count_read=row_count_read,
            column_count_read=column_count_read,
            total_row_count=total_row_count,
            total_column_count=total_column_count,
        )
        # Return the data part directly as per tool return type
        return result.data

    except Exception as e:
        logger.error(f"Error reading sheet '{sheet_name}' in {file_path}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error processing Excel for reading sheet: {str(e)}")


@mcp.tool(description="Converts data from a specific sheet or range in an Excel file to CSV format (string).")
async def convert_sheet_to_csv(
    file_path: str = Field(description="The absolute path to the Excel file (.xlsx or .xls)."),
    sheet_name: str = Field(description="The name of the sheet."),
    start_row: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed starting row to read from (inclusive).",
    ),
    end_row: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed ending row to read up to (inclusive).",
    ),
    start_column: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed starting column to read from (inclusive).",
    ),
    end_column: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed ending column to read up to (inclusive).",
    ),
) -> str:
    """
    Converts data from a specific sheet or range in an Excel file to CSV format string.

    Args:
        file_path: The absolute path to the Excel file.
        sheet_name: The name of the sheet.
        start_row: Optional. The 0-indexed starting row (inclusive).
        end_row: Optional. The 0-indexed ending row (inclusive).
        start_column: Optional. The 0-indexed starting column (inclusive).
        end_column: Optional. The 0-indexed ending column (inclusive).

    Returns:
        A string containing the CSV representation of the data.

    Raises:
        FileNotFoundError: If the specified file_path does not exist.
        ValueError: If the sheet name is not found, file format is unsupported, or invalid parameters.
        RuntimeError: If required libraries are not installed or an error occurs.
    """
    error = check_file_readable(file_path)
    if error:
        logger.error(error)
        raise FileNotFoundError(error)

    try:
        xls = read_excel_file(file_path)
        if sheet_name not in xls.sheet_names:
            raise ValueError(
                f"Sheet '{sheet_name}' not found in file '{file_path}'. Available sheets: {xls.sheet_names}"
            )

        # Determine rows to read based on start_row and end_row
        skiprows = start_row if start_row is not None else 0
        nrows = None
        if end_row is not None:
            nrows = end_row - skiprows + 1

        # Determine columns to read based on start_column and end_column
        usecols = None
        if start_column is not None or end_column is not None:
            col_start = start_column if start_column is not None else 0
            col_end = end_column if end_column is not None else float("inf")
            usecols = list(range(col_start, int(col_end) + 1))

        # Read the sheet into a pandas DataFrame
        header = 0 if skiprows == 0 else None
        df = xls.parse(sheet_name, skiprows=skiprows, nrows=nrows, usecols=usecols, header=header)

        # Convert DataFrame to CSV string
        csv_data = df.to_csv(index=False)

        result = CsvContent(file_path=file_path, sheet_name=sheet_name, csv_data=csv_data)
        return result.csv_data

    except Exception as e:
        logger.error(f"Error converting sheet '{sheet_name}' in {file_path} to CSV: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error processing Excel for CSV conversion: {str(e)}")


@mcp.tool(
    description="Converts data from a specific sheet or range in an Excel file to Markdown table format (string)."
)
async def convert_sheet_to_markdown(
    file_path: str = Field(description="The absolute path to the Excel file (.xlsx or .xls)."),
    sheet_name: str = Field(description="The name of the sheet."),
    start_row: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed starting row to read from (inclusive).",
    ),
    end_row: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed ending row to read up to (inclusive).",
    ),
    start_column: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed starting column to read from (inclusive).",
    ),
    end_column: Optional[int] = Field(
        default=None,
        description="Optional. The 0-indexed ending column to read up to (inclusive).",
    ),
    max_rows: Optional[int] = Field(
        default=None,
        description="Optional. Maximum number of rows to read from the starting row. "
        "Applied after start_row/end_row if specified.",
    ),
    max_columns: Optional[int] = Field(
        default=None,
        description="Optional. Maximum number of columns to read from the starting column. "
        "Applied after start_column/end_column if specified.",
    ),
) -> str:
    """
    Converts data from a specific sheet or range in an Excel file to Markdown table format string.

    Args:
        file_path: The absolute path to the Excel file.
        sheet_name: The name of the sheet.
        start_row: Optional. The 0-indexed starting row (inclusive).
        end_row: Optional. The 0-indexed ending row (inclusive).
        start_column: Optional. The 0-indexed starting column (inclusive).
        end_column: Optional. The 0-indexed ending column (inclusive).
        max_rows: Optional. Maximum number of rows to read.
        max_columns: Optional. Maximum number of columns to read.

    Returns:
        A string containing the Markdown table representation of the data.

    Raises:
        FileNotFoundError: If the specified file_path does not exist.
        ValueError: If the sheet name is not found, file format is unsupported, or invalid parameters.
        RuntimeError: If required libraries are not installed or an error occurs.
    """
    error = check_file_readable(file_path)
    if error:
        logger.error(error)
        raise FileNotFoundError(error)

    try:
        xls = read_excel_file(file_path)
        if sheet_name not in xls.sheet_names:
            raise ValueError(
                f"Sheet '{sheet_name}' not found in file '{file_path}'. Available sheets: {xls.sheet_names}"
            )

        # Determine rows to read based on start_row, end_row, and max_rows
        skiprows = start_row if start_row is not None else 0
        nrows = None
        if end_row is not None:
            nrows = end_row - skiprows + 1
        if max_rows is not None:
            if nrows is None:
                nrows = max_rows
            else:
                nrows = min(nrows, max_rows)

        # Determine columns to read based on start_column, end_column, and max_columns
        usecols = None
        if start_column is not None or end_column is not None or max_columns is not None:
            col_start = start_column if start_column is not None else 0
            col_end = end_column if end_column is not None else float("inf")
            col_limit = max_columns if max_columns is not None else float("inf")

            actual_col_end = min(col_end, col_start + col_limit - 1) if col_limit is not float("inf") else col_end
            if actual_col_end < col_start:
                usecols = []  # No columns to read
            else:
                usecols = list(range(col_start, int(actual_col_end) + 1))

        # Read the sheet into a pandas DataFrame
        header = 0 if skiprows == 0 else None
        df = xls.parse(sheet_name, skiprows=skiprows, nrows=nrows, usecols=usecols, header=header)

        # Convert DataFrame to Markdown table string
        markdown_table = tabulate(df, headers="keys", tablefmt="pipe")

        result = MarkdownTableContent(file_path=file_path, sheet_name=sheet_name, markdown_table=markdown_table)
        return result.markdown_table

    except Exception as e:
        logger.error(f"Error converting sheet '{sheet_name}' in {file_path} to Markdown: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error processing Excel for Markdown conversion: {str(e)}")


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
