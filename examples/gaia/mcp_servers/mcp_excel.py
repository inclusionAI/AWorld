import logging
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Union

import pandas as pd
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

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
    truncated: bool = False


class CsvContent(BaseModel):
    """Model fcontent."""

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
    if not os.path.exists(file_path):
        return f"File does not exist: {file_path}"
    if not os.access(file_path, os.R_OK):
        return f"File is not readable: {file_path}"
    if not (file_path.lower().endswith(".xlsx") or file_path.lower().endswith(".xls")):
        return f"Unsupported file format. Only .xlsx and .xls are supported: {file_path}"
    return None


# Helper to read Excel
def read_table_file(file_path: str):
    return pd.ExcelFile(file_path)


def read_excel_file(file_path: str):
    """Helper function to read Excel file using pandas."""
    if file_path.lower().endswith(".xls") and xlrd is None:
        raise RuntimeError("xlrd library is required to read .xls files but not found.")
    return pd.ExcelFile(file_path)


@mcp.tool(description="Retrieves metadata from an Excel file (e.g., sheet names, number of sheets).")
async def get_excel_metadata(
    file_path: str = Field(description="The absolute path to the Excel file (.xlsx, .xls, )."),
) -> Dict[str, Any]:
    """
    Retrieves metadata from an Excel file.
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
        raise RuntimeError(f"Error processing file for metadata: {str(e)}")


@mcp.tool(description="Retrieves the number of rows and columns for a specific sheet in an Excel file.")
async def get_sheet_dimensions(
    file_path: str = Field(description="The absolute path to the Excel file (.xlsx, .xls, )."),
    sheet_name: str = Field(description="The name of the sheet."),
) -> Dict[str, int]:
    """
    Retrieves the number of rows and columns for a specific sheet in an Excel file.
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
        raise RuntimeError(f"Error processing file for sheet dimensions: {str(e)}")


@mcp.tool(description="Reads data from a specific sheet in an Excel file. Return sheet as a list of dicts.")
async def read_excel_sheet(
    file_path: str = Field(description="The absolute path to the Excel file (.xlsx, .xls, )."),
    sheet_name: str = Field(description="The name of the sheet."),
) -> SheetData:
    """
    Reads data from a specific sheet in an Excel file. Returns sheet as a list of dictionaries (one per row).
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
        row_count_read = df.shape[0]
        column_count_read = df.shape[1]
        total_row_count = None
        total_column_count = None
        try:
            full_df = xls.parse(sheet_name)
            total_row_count = full_df.shape[0]
            total_column_count = full_df.shape[1]
        except Exception:
            pass
        data = df.to_dict(orient="records")
        return SheetData(
            file_path=file_path,
            sheet_name=sheet_name,
            data=data,
            row_count_read=row_count_read,
            column_count_read=column_count_read,
            total_row_count=total_row_count,
            total_column_count=total_column_count,
            error=False,
            truncated=False,
        )
    except Exception as e:
        logger.error(f"Error reading sheet '{sheet_name}' in {file_path}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Error processing file for reading sheet: {str(e)}")


# @mcp.tool(description="Converts data from a specific sheet or range in an Excel file to CSV format (string).")
# async def convert_sheet_to_csv(
#     file_path: str = Field(description="The absolute path to the Excel file (.xlsx, .xls, )."),
#     sheet_name: str = Field(description="The name of the sheet."),
# ) -> str:
#     """
#     Converts data from a specific sheet or range in an Excel file to CSV format string.
#     """
#     error = check_file_readable(file_path)
#     if error:
#         logger.error(error)
#         raise FileNotFoundError(error)
#     try:
#         xls = read_excel_file(file_path)
#         if sheet_name not in xls.sheet_names:
#             raise ValueError(
#                 f"Sheet '{sheet_name}' not found in file '{file_path}'. Available sheets: {xls.sheet_names}"
#             )
#         df: pd.DataFrame = xls.parse(sheet_name)
#         csv_data = df.to_csv(index=False)
#         result = CsvContent(file_path=file_path, sheet_name=sheet_name, csv_data=csv_data)
#         return result.csv_data
#     except Exception as e:
#         logger.error(f"Error converting sheet '{sheet_name}' in {file_path} to CSV: {e}\n{traceback.format_exc()}")
#         raise RuntimeError(f"Error processing file fconversion: {str(e)}")


# @mcp.tool(
#     description="Converts data from a specific sheet or range in an Excel file to Markdown table format (string)."
# )
# async def convert_sheet_to_markdown(
#     file_path: str = Field(description="The absolute path to the Excel file (.xlsx, .xls, )."),
#     sheet_name: str = Field(description="The name of the sheet."),
# ) -> str:
#     """
#     Converts data from a specific sheet or range in an Excel file to Markdown table format string.
#     """
#     error = check_file_readable(file_path)
#     if error:
#         logger.error(error)
#         raise FileNotFoundError(error)
#     try:
#         xls = read_excel_file(file_path)
#         if sheet_name not in xls.sheet_names:
#             raise ValueError(
#                 f"Sheet '{sheet_name}' not found in file '{file_path}'. Available sheets: {xls.sheet_names}"
#             )
#         df = xls.parse(sheet_name)
#         markdown_table = tabulate(df, headers="keys", tablefmt="pipe")
#         result = MarkdownTableContent(file_path=file_path, sheet_name=sheet_name, markdown_table=markdown_table)
#         return result.markdown_table
#     except Exception as e:
#         logger.error(f"Error converting sheet '{sheet_name}' in {file_path} to Markdown: {e}\n{traceback.format_exc()}")
#         raise RuntimeError(f"Error processing file for Markdown conversion: {str(e)}")


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
