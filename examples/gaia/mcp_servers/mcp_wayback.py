import logging
import sys
import traceback
from typing import Any, Dict, List, Optional, Union

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import Field
from waybackpy import WaybackMachineCDXServerAPI, WaybackMachineSaveAPI
from waybackpy.cdx_snapshot import CDXSnapshot

# pylint: disable=W0707
# Initialize logger
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# Initialize MCP server
mcp = FastMCP("wayback-machine-server")


@mcp.tool(description="Lists available archived versions (snapshots) for a given URL.")
async def list_available_versions(
    url: str = Field(description="The URL of the website to check."),
    limit: Optional[int] = Field(
        default=10,
        description="Maximum number of versions to return. Set to 0 for all (can be large).",
    ),
    from_date: Optional[str] = Field(
        default=None,
        description="Start date for filtering versions (YYYYMMDDhhmmss).",
    ),
    to_date: Optional[str] = Field(
        default=None,
        description="End date for filtering versions (YYYYMMDDhhmmss).",
    ),
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Lists available archived versions (snapshots) for a given URL from the WayBack Machine.

    Args:
        url: The URL of the website.
        limit: Maximum number of versions to return. If 0, returns all.
        from_date: Start date for filtering (YYYYMMDDhhmmss).
        to_date: End date for filtering (YYYYMMDDhhmmss).

    Returns:
        A list of dictionaries, each representing a version (timestamp, URL),
        or a summary dictionary if the list is extensive and limit is used.

    Raises:
        RuntimeError: If waybackpy library is not installed or if data cannot be fetched.
        ValueError: If the URL is invalid or no versions are found.
    """
    if WaybackMachineCDXServerAPI is None:
        raise RuntimeError("waybackpy library is not installed.")

    try:
        # Use WaybackMachineCDXServerAPI to query available snapshots
        # The user_agent is required by WayBack Machine API
        user_agent = "AWorld/1.0 (https://github.com/inclusionAI/AWorld; qintong.wqt@antgroup.com)"
        cdx_api = WaybackMachineCDXServerAPI(url, user_agent=user_agent)

        # Fetch all snapshots, then filter by date range
        all_snapshots: List[CDXSnapshot] = list(cdx_api.snapshots())
        if from_date or to_date:
            snapshots = [
                s
                for s in all_snapshots
                if (not from_date or s.timestamp >= from_date) and (not to_date or s.timestamp <= to_date)
            ]
        else:
            snapshots = all_snapshots

        if not snapshots:
            raise ValueError(f"No archived versions found for URL: {url}")

        # Convert snapshots to a list of dictionaries
        version_list = []
        for snapshot in snapshots:
            version_list.append(
                {
                    "timestamp": snapshot.timestamp,
                    "url": snapshot.archive_url,
                    "status_code": snapshot.statuscode,
                    "digest": snapshot.digest,
                    "length": snapshot.length,
                    "mime_type": snapshot.mimetype,
                }
            )

        # LLM-friendly preview logic
        if limit is not None and limit > 0 and len(version_list) > limit:
            preview_count = limit // 2
            if preview_count == 0:
                preview_count = 1  # ensure at least one row from start/end if limit is 1 or 2

            summary_data = {
                "message": f"Found {len(version_list)} archived versions. Showing a preview.",
                "total_versions": len(version_list),
                "url": url,
                "from_date": from_date,
                "to_date": to_date,
                "versions_preview_start": version_list[:preview_count],
                "versions_preview_end": version_list[-preview_count:],
            }
            return summary_data
        else:
            return version_list

    except ValueError as ve:
        logger.error(f"Value error for listing versions for {url}: {ve}")
        raise
    except Exception as e:
        logger.error(f"Error listing versions for {url}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Failed to list versions for {url}: {str(e)}")


@mcp.tool(description="Fetches the content of a specific archived page version.")
async def get_archived_page_content(
    url: str = Field(description="The URL of the website."),
    timestamp: str = Field(description="The timestamp of the desired version (YYYYMMDDhhmmss)."),
    is_truncated: bool = Field(
        default=False,  # Limit content length for LLM friendliness
        description="Set to False for full webpage content. Otherwise, "
        "the content is truncated to the first 8192 characters.",
    ),
    extract_text_only: bool = Field(
        default=True,
        description="If true, attempts to extract only the main text content, ignoring HTML tags.",
    ),
) -> Dict[str, Any]:
    """
    Fetches the content of a specific archived page version from the WayBack Machine.

    Args:
        url: The URL of the website.
        timestamp: The timestamp of the desired version (YYYYMMDDhhmmss).
        content_limit: Maximum length of the returned content. 0 for full content.
        extract_text_only: If true, attempts to return only the main text content.

    Returns:
        A dictionary containing the URL, timestamp, and the fetched content (potentially truncated or text-only).

    Raises:
        RuntimeError: If waybackpy library is not installed or if data cannot be fetched.
        ValueError: If the URL or timestamp is invalid, or content cannot be retrieved.
    """
    TRUNCATED_LENGTH = 8 * 1024

    if WaybackMachineCDXServerAPI is None:
        raise RuntimeError("waybackpy library is not installed.")

    try:
        user_agent = "AWorld/1.0 (https://github.com/inclusionAI/AWorld; qintong.wqt@antgroup.com)"
        cdx_api = WaybackMachineCDXServerAPI(url, user_agent=user_agent)

        # Find the closest snapshot to the given timestamp
        snapshot: CDXSnapshot = cdx_api.near(wayback_machine_timestamp=timestamp)

        if not snapshot or not snapshot.archive_url:
            raise ValueError(f"No archived version found for URL {url} at timestamp {timestamp}.")

        # Fetch the content of the snapshot
        response = requests.get(snapshot.archive_url, timeout=10)
        page_content = response.text

        if extract_text_only:
            # Simple text extraction (can be improved with libraries like BeautifulSoup)
            # For now, just remove basic HTML tags as a simple approach
            # page_content = re.sub(r"<.*?>", "", page_content)
            # page_content = re.sub(r"\s+", " ", page_content).strip()  # Normalize whitespace
            # Alternative with BeautifulSoup (uncomment imports above):
            soup = BeautifulSoup(page_content, "html.parser")
            page_content = soup.get_text(separator=" ", strip=True)

        original_length = len(page_content)
        truncated = False
        if is_truncated and len(page_content) > TRUNCATED_LENGTH:
            page_content = page_content[:TRUNCATED_LENGTH] + "..."  # Truncate and add ellipsis
            truncated = True

        return {
            "url": url,
            "timestamp": timestamp,
            "fetched_timestamp": snapshot.timestamp,  # Actual timestamp fetched (closest)
            "content": page_content,
            "original_content_length": original_length,
            "truncated": truncated,
            "extract_text_only": extract_text_only,
            "message": "Content fetched successfully."
            + (" Content truncated." if truncated else "")
            + (" Text extracted." if extract_text_only else ""),
        }

    except ValueError as ve:
        logger.error(f"Value error for getting page content for {url} at {timestamp}: {ve}")
        raise
    except Exception as e:
        logger.error(f"Error fetching page content for {url} at {timestamp}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Failed to fetch page content for {url} at {timestamp}: {str(e)}")


# Optional: Add a tool to save a current page to WayBack Machine
@mcp.tool(description="Saves the current state of a given URL to the WayBack Machine.")
async def save_page_to_wayback(url: str = Field(description="The URL of the website to save.")) -> Dict[str, Any]:
    """
    Saves the current state of a given URL to the WayBack Machine.

    Args:
        url: The URL of the website to save.
        capture_all: If true, attempts to capture all linked assets.

    Returns:
        A dictionary containing the URL and the URL of the saved archive page.

    Raises:
        RuntimeError: If waybackpy library is not installed or if saving fails.
        ValueError: If the URL is invalid.
    """
    if WaybackMachineSaveAPI is None:
        raise RuntimeError("waybackpy library is not installed.")

    try:
        user_agent = "AWorld/1.0 (https://github.com/inclusionAI/AWorld; qintong.wqt@antgroup.com)"
        save_api = WaybackMachineSaveAPI(url, user_agent=user_agent)

        # Save the page
        archive_url = save_api.save()

        if not archive_url:
            raise RuntimeError(f"Failed to save URL {url} to WayBack Machine.")

        return {
            "url": url,
            "archive_url": archive_url,
            "message": f"Page saved successfully. Archived URL: {archive_url}",
        }

    except ValueError as ve:
        logger.error(f"Value error for saving page {url}: {ve}")
        raise
    except Exception as e:
        logger.error(f"Error saving page {url}: {e}\n{traceback.format_exc()}")
        raise RuntimeError(f"Failed to save page {url}: {str(e)}")


def main():
    """
    Main function to start the MCP server.
    """
    load_dotenv()  # Load environment variables from .env file if present
    logger.info("Starting WayBack Machine MCP Server...")
    mcp.run(transport="stdio")


# Make the module callable
def __call__():
    """
    Make the module callable for uvx.
    This function is called when the module is executed directly by uvx.
    """
    main()


sys.modules[__name__].__call__ = __call__

# Run the server when the script is executed directly
if __name__ == "__main__":
    main()
