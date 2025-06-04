import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field

from aworld.logs.util import logger

# Attempt to import requests and provide a clear error if not found
try:
    import requests
except ImportError:
    logger.error(
        "The 'requests' library is not installed. "
        "Download functionality via 'download_file' tool will not work. "
        "Please install it by running 'pip install requests'."
    )
    requests = None

# Initialize MCP server
mcp = FastMCP("download-server")


@mcp.tool(
    description="Downloads a file from a given URL to a specified local path. "
    "Handles HTTP/HTTPS URLs and provides status feedback."
)
async def download_file(
    url: str = Field(description="The HTTP/HTTPS URL of the file to download."),
    output_file_path: str = Field(
        description="The absolute path (including filename) where the downloaded file should be saved.",
    ),
    overwrite: bool = Field(
        default=False,
        description="If True, overwrite the file if it already exists at output_file_path. Default is False.",
    ),
    timeout: Optional[int] = Field(
        default=60,
        description="Optional. Timeout for the download request in seconds. Default is 60 seconds.",
    ),
    headers: Optional[Dict[str, str]] = Field(
        default=None,
        description="Optional. Custom headers to include in the download request (e.g., for authorization).",
    ),
) -> Dict[str, Any]:
    """
    Downloads a file from a URL and saves it locally.

    Args:
        url: The URL of the file to download.
        output_file_path: The absolute path to save the downloaded file.
        overwrite: Whether to overwrite the file if it exists.
        timeout: Request timeout in seconds.
        headers: Custom headers for the request.

    Returns:
        A dictionary containing the status of the download, the file path,
        size of the downloaded file, and an error message if applicable.
    """
    if requests is None:
        logger.error("Requests library is not available. Cannot download_file.")
        return {
            "status": "error",
            "message": "The 'requests' library is not installed or available on the server.",
            "file_path": None,
            "size_bytes": None,
        }

    if not os.path.isabs(output_file_path):
        fs_dir = os.getenv("FILESYSTEM_SERVER_WORKDIR")
        if fs_dir and os.path.isabs(fs_dir):
            logger.warning(
                f"Output path is not absolute: {output_file_path}. Use enviroment variable instead: {fs_dir}"
            )
            output_file_path = Path(fs_dir) / output_file_path
        else:
            return {
                "status": "error",
                "message": f"Output path must be an absolute path. Provided: {output_file_path}",
                "file_path": None,
                "size_bytes": None,
            }

    output_dir = os.path.dirname(output_file_path)
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"Created output directory: {output_dir}")
    except OSError as e:
        logger.error(f"Failed to create output directory {output_dir}: {e}")
        return {
            "status": "error",
            "message": f"Failed to create output directory {output_dir}: {str(e)}",
            "file_path": None,
            "size_bytes": None,
        }

    if os.path.exists(output_file_path) and not overwrite:
        logger.warning(f"File {output_file_path} already exists and overwrite is False.")
        return {
            "status": "error",
            "message": f"File already exists at {output_file_path} and overwrite is set to False.",
            "file_path": output_file_path,
            "size_bytes": os.path.getsize(output_file_path) if os.path.exists(output_file_path) else None,
        }

    try:
        logger.info(f"Attempting to download file from {url} to {output_file_path}")
        with requests.get(url, stream=True, timeout=timeout, headers=headers) as r:
            r.raise_for_status()  # Raises an HTTPError for bad responses (4XX or 5XX)
            with open(output_file_path, "wb") as f:
                shutil.copyfileobj(r.raw, f)
        file_size = os.path.getsize(output_file_path)
        logger.info(f"Successfully downloaded {url} to {output_file_path} ({file_size} bytes).")
        return {
            "status": "success",
            "file_path": output_file_path if os.path.isabs(output_file_path) else Path(output_dir) / output_file_path,
            "size_bytes": file_size,
            "message": "File downloaded successfully.",
        }
    except requests.exceptions.Timeout as e:
        logger.error(f"Download timeout for {url}: {e}")
        return {
            "status": "error",
            "message": f"Download timed out after {timeout} seconds for URL: {url}. Error: {str(e)}",
            "file_path": None,
            "size_bytes": None,
        }
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading file from {url}: {e}")
        return {
            "status": "error",
            "message": f"Failed to download file from {url}. Error: {str(e)}",
            "file_path": None,
            "size_bytes": None,
        }
    except IOError as e:
        logger.error(f"IOError writing file to {output_file_path}: {e}")
        return {
            "status": "error",
            "message": f"Failed to write downloaded file to {output_file_path}. Error: {str(e)}",
            "file_path": None,
            "size_bytes": None,
        }
    except Exception as e:
        logger.error(f"An unexpected error occurred during download from {url}: {e}")
        return {
            "status": "error",
            "message": f"An unexpected error occurred: {str(e)}",
            "file_path": None,
            "size_bytes": None,
        }


def main():
    """
    Main function to start the MCP server.
    Loads environment variables and runs the server.
    """
    load_dotenv()  # Load environment variables from .env file
    logger.info("Starting Download MCP Server...")
    mcp.run(transport="stdio")
    logger.info("Download MCP Server stopped.")


# Make the module callable for uvx (similar to other AWorld MCP servers)
def __call__():
    """
    Makes the module callable, typically for execution via tools like uvx.
    """
    main()


# Add this for compatibility with uvx and direct execution
if __name__ != "__main__":  # Ensure __call__ is set when imported as a module
    sys.modules[__name__].__call__ = __call__

# Run the server when the script is executed directly
if __name__ == "__main__":
    main()
