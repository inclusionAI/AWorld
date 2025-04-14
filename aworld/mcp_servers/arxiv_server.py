"""
ArXiv MCP Server

This module provides MCP server functionality for interacting with the arXiv API.
It allows for searching and downloading academic articles from arXiv based on various
criteria and filters.

Key features:
- Search for articles using queries and filters
- Download PDF files of articles by their IDs
- Convert search results to structured models

Main functions:
- mcpsearch: Searches for articles on arXiv
- mcpdownload: Downloads PDF files of arXiv articles
"""

import json
import os
from pathlib import Path
from typing import List, Optional

import arxiv
from pydantic import BaseModel, Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server


class ArxivArticle(BaseModel):
    """Model representing an arXiv article with essential metadata"""

    entry_id: str
    title: str
    authors: List[str]
    summary: str
    published: str
    updated: str
    pdf_url: str
    categories: List[str]
    doi: Optional[str] = None
    comment: Optional[str] = None
    journal_ref: Optional[str] = None


class ArxivSearchResult(BaseModel):
    """Model representing search results from arXiv"""

    total_results: int
    articles: List[ArxivArticle]


class ArxivDownloadResult(BaseModel):
    """Model representing the result of downloading an arXiv article"""

    entry_id: str
    title: str
    file_path: str
    file_size: int
    success: bool
    error: Optional[str] = None


def _convert_result_to_model(result: arxiv.Result) -> ArxivArticle:
    """Convert arxiv.Result to ArxivArticle model

    Args:
        result: The arxiv.Result object to convert

    Returns:
        ArxivArticle: The converted article model
    """
    return ArxivArticle(
        entry_id=result.entry_id,
        title=result.title,
        authors=[author.name for author in result.authors],
        summary=result.summary,
        published=result.published.isoformat() if result.published else None,
        updated=result.updated.isoformat() if result.updated else None,
        pdf_url=result.pdf_url,
        categories=result.categories,
        doi=result.doi,
        comment=result.comment,
        journal_ref=result.journal_ref,
    )


def mcpsearch(
    query: str = Field(..., description="Search query for arXiv articles"),
    max_results: int = Field(
        10, description="Maximum number of results to return (default: 10)"
    ),
    sort_by: str = Field(
        "relevance",
        description="Sort order: 'relevance' (default), 'lastUpdatedDate', or 'submittedDate'",
    ),
    sort_order: str = Field(
        "descending",
        description="Sort direction: 'descending' (default) or 'ascending'",
    ),
    categories: List[str] = Field(
        [],
        description="List of arXiv categories to filter by (e.g., 'cs.AI', 'math.CO')",
    ),
) -> str:
    """Search for articles on arXiv based on the provided query and filters

    Args:
        query: Search query string
        max_results: Maximum number of results to return
        sort_by: Sort order (relevance, lastUpdatedDate, submittedDate)
        sort_order: Sort direction (descending, ascending)
        categories: List of arXiv categories to filter by

    Returns:
        JSON string with search results
    """
    try:
        # Map sort_by to arxiv.SortCriterion
        sort_criteria_map = {
            "relevance": arxiv.SortCriterion.Relevance,
            "lastUpdatedDate": arxiv.SortCriterion.LastUpdatedDate,
            "submittedDate": arxiv.SortCriterion.SubmittedDate,
        }

        # Map sort_order to arxiv.SortOrder
        sort_order_map = {
            "descending": arxiv.SortOrder.Descending,
            "ascending": arxiv.SortOrder.Ascending,
        }

        # Validate inputs
        if sort_by not in sort_criteria_map:
            raise ValueError(
                f"Invalid sort_by value. Must be one of: {', '.join(sort_criteria_map.keys())}"
            )

        if sort_order not in sort_order_map:
            raise ValueError(
                f"Invalid sort_order value. Must be one of: {', '.join(sort_order_map.keys())}"
            )

        # Create search client
        client = arxiv.Client()

        # Build search query
        search = arxiv.Search(
            query=query,
            max_results=max_results,
            sort_by=sort_criteria_map[sort_by],
            sort_order=sort_order_map[sort_order],
            categories=categories if categories else None,
        )

        # Execute search
        logger.info(f"Searching arXiv for: {query}")
        results = list(client.results(search))

        # Convert results to our model
        articles = [_convert_result_to_model(result) for result in results]

        # Create search result
        search_result = ArxivSearchResult(
            total_results=len(articles), articles=articles
        )

        return search_result.model_dump_json()

    except Exception as e:
        error_msg = str(e)
        logger.error(f"arXiv search error: {error_msg}")
        return json.dumps({"error": error_msg})


def mcpdownload(
    article_ids: List[str] = Field(
        ...,
        description="List of arXiv article IDs to download (e.g., ['2307.09288', '2103.00020'])",
    ),
    output_dir: str = Field(
        "/tmp/arxiv",
        description="Directory to save the downloaded PDFs (default: /tmp/arxiv)",
    ),
) -> str:
    """Download PDF files of arXiv articles based on their IDs

    Args:
        article_ids: List of arXiv article IDs to download
        output_dir: Directory to save the downloaded PDFs

    Returns:
        JSON string with download results
    """
    try:
        # Create output directory if it doesn't exist
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Create search client
        client = arxiv.Client()

        # Prepare results list
        download_results = []

        # Process each article ID
        for article_id in article_ids:
            try:
                # Normalize article ID format
                if not article_id.startswith("http"):
                    # Add arXiv prefix if not already a full URL
                    if "/" not in article_id:
                        # Convert new-style ID (e.g., 2307.09288) to query format
                        search_id = f"id:{article_id}"
                    else:
                        # Handle old-style IDs (e.g., math/0211159)
                        search_id = f"id:arxiv:{article_id}"
                else:
                    # Extract ID from URL
                    search_id = f"id:{article_id.split('/')[-1]}"

                # Search for the specific article
                search = arxiv.Search(query=search_id, max_results=1)

                # Get the article
                results = list(client.results(search))

                if not results:
                    raise ValueError(f"Article with ID {article_id} not found")

                article = results[0]

                # Generate filename from article title
                safe_title = "".join(
                    c if c.isalnum() or c in " -_" else "_" for c in article.title
                )
                safe_title = safe_title[:100]  # Limit length
                filename = f"{article_id.replace('/', '_')}_{safe_title}.pdf"
                file_path = os.path.join(output_dir, filename)

                # Download the article
                logger.info(f"Downloading article: {article.title} (ID: {article_id})")
                article.download_pdf(filename=file_path)

                # Get file size
                file_size = os.path.getsize(file_path)

                # Create result
                result = ArxivDownloadResult(
                    entry_id=article.entry_id,
                    title=article.title,
                    file_path=file_path,
                    file_size=file_size,
                    success=True,
                    error=None,
                )

                download_results.append(result)

            except Exception as article_error:
                # Handle individual article download errors
                error_msg = str(article_error)
                logger.error(f"Error downloading article {article_id}: {error_msg}")

                result = ArxivDownloadResult(
                    entry_id=article_id,
                    title="",
                    file_path="",
                    file_size=0,
                    success=False,
                    error=error_msg,
                )

                download_results.append(result)

        # Return results
        return json.dumps(
            {
                "total": len(article_ids),
                "success_count": sum(1 for r in download_results if r.success),
                "failed_count": sum(1 for r in download_results if not r.success),
                "results": [r.model_dump() for r in download_results],
            }
        )

    except Exception as e:
        error_msg = str(e)
        logger.error(f"arXiv download error: {error_msg}")
        return json.dumps({"error": error_msg})


if __name__ == "__main__":
    run_mcp_server("arXiv Server", funcs=[mcpsearch, mcpdownload], port=7777)
