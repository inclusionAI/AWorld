import os
import urllib.parse
from pathlib import Path
from typing import Optional

import requests
from pydantic import BaseModel, Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server


class DownloadResult(BaseModel):
    """Download result model with file information"""

    file_path: str
    file_name: str
    file_size: int
    content_type: Optional[str] = None
    success: bool
    error: Optional[str] = None


def mcpdownload(
    url: str = Field(..., description="The URL of the file to download."),
    output_dir: str = Field(
        "/tmp", description="Directory to save the downloaded file (default: /tmp)."
    ),
    filename: str = Field(
        "", description="Custom filename for the downloaded file (optional)."
    ),
    timeout: int = Field(60, description="Download timeout in seconds (default: 60)."),
) -> str:
    """Download a file from a URL and save it to the local filesystem.

    Args:
        url: The URL of the file to download
        output_dir: Directory to save the downloaded file
        filename: Custom filename for the downloaded file (if empty, derived from URL)
        timeout: Download timeout in seconds

    Returns:
        JSON string with download result information
    """
    try:
        # Validate URL
        if not url.startswith(("http://", "https://")):
            raise ValueError(
                "Invalid URL format. URL must start with http:// or https://"
            )

        # Create output directory if it doesn't exist
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Determine filename if not provided
        if not filename:
            filename = os.path.basename(urllib.parse.urlparse(url).path)
            if not filename:
                filename = "downloaded_file"

        # Full path to save the file
        file_path = os.path.join(output_path, filename)

        logger.info(f"Downloading file from {url} to {file_path}")

        # Download the file with progress tracking
        response = requests.get(url, stream=True, timeout=timeout)
        response.raise_for_status()

        # Get content type and size
        content_type = response.headers.get("Content-Type")
        file_size = int(response.headers.get("Content-Length", 0))

        # Save the file
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        # Get actual file size
        actual_size = os.path.getsize(file_path)

        logger.success(f"File downloaded successfully to {file_path}")

        # Create result
        result = DownloadResult(
            file_path=file_path,
            file_name=filename,
            file_size=actual_size,
            content_type=content_type,
            success=True,
            error=None,
        )

        return result.model_dump_json()

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Download error: {error_msg}")

        result = DownloadResult(
            file_path="",
            file_name="",
            file_size=0,
            content_type=None,
            success=False,
            error=error_msg,
        )

        return result.model_dump_json()


if __name__ == "__main__":
    run_mcp_server("Download Server", funcs=[mcpdownload], port=6666)
