import json
import os
from typing import List, Optional

from exa_py import Exa
from exa_py.api import ResultWithText, SearchResponse
from pydantic import BaseModel, Field, field_validator

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server


class ExaSearchResult(BaseModel):
    """Search result model with validation"""

    id: str
    title: str
    url: str
    publishedDate: str
    author: str
    score: str
    text: str
    image: str
    favicon: str

    @field_validator("url")
    def validate_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("Invalid URL format")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "id": "123",
                "title": "Sample Title",
                "url": "https://example.com",
                "publishedDate": "2023-01-01",
                "author": "John Doe",
                "score": "0.95",
                "text": "Sample text",
                "image": "https://example.com/image.jpg",
                "favicon": "https://example.com/favicon.ico",
            }
        }
        json_encoders = {
            "publishedDate": lambda v: (
                v.isoformat() if hasattr(v, "isoformat") else str(v)
            )
        }
        populate_by_name = True
        validate_assignment = True
        arbitrary_types_allowed = True


def mcpsearchquery(
    query: str = Field(..., description="The query string."),
) -> str:
    """Search the web using Exa with a query to retrieve relevant results."""
    try:
        api_key = os.environ.get("EXA_API_KEY")
        if not api_key:
            raise ValueError("EXA_API_KEY environment variable not set")
        exa = Exa(api_key=api_key)
        logger.success(f"Search starts for query: {query}")
        search_results = exa.search_and_contents(query, text=True)
        logger.success(f"Search ends for query: {query}")

        results = build_response(search_results)
        # Convert Pydantic models to dictionaries before JSON serialization
        serializable_results = [
            result.model_dump() for result in results[: min(3, len(results))]
        ]
        return json.dumps(serializable_results)

    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        return json.dumps({"error": str(e)})


def mcpsearch(
    query: str = Field(..., description="The query string."),
    num_results: str = Field(
        "20", description="Number of search results to return (default 20)."
    ),
    include_domains: List[str] = Field(
        None, description="Domains to include in the search."
    ),
    exclude_domains: List[str] = Field(
        None, description="Domains to exclude from the search."
    ),
    start_crawl_date: str = Field(
        None, description="Only links crawled after this date."
    ),
    end_crawl_date: str = Field(
        None, description="Only links crawled before this date."
    ),
    start_published_date: str = Field(
        None, description="Only links published after this date."
    ),
    end_published_date: str = Field(
        None, description="Only links published before this date."
    ),
    include_text: List[str] = Field(
        None, description="Strings that must appear in the page text."
    ),
    exclude_text: List[str] = Field(
        None, description="Strings that must not appear in the page text."
    ),
    use_autoprompt: bool = Field(
        False, description="Convert query to Exa (default False)."
    ),
    type: str = Field(
        "neural", description="'keyword' or 'neural' (default 'neural')."
    ),
    category: str = Field(
        "",
        description="available options: (company, research paper, news, pdf, github, tweet, personal site, linkedin profile, financial report)",
    ),
    flags: List[str] = Field(None, description="Experimental flags for Exa usage."),
    moderation: bool = Field(
        False, description="If True, the search results will be moderated for safety."
    ),
    text: bool = Field(
        False, description="Whether to include webpage contents in results."
    ),
) -> List[str]:
    """Search the web using Exa with a query to retrieve relevant results."""
    try:
        api_key = os.environ.get("EXA_API_KEY")
        if not api_key:
            raise ValueError("EXA_API_KEY environment variable not set")

        if type and type not in ["keyword", "neural"]:
            raise ValueError("Search type must be either 'keyword' or 'neural'")

        if start_published_date and end_published_date:
            if start_published_date > end_published_date:
                raise ValueError(
                    "start_published_date cannot be later than end_published_date"
                )

        exa = Exa(api_key=api_key)
        logger.info(f"Search starts for query: {query}")
        search_results = exa.search_and_contents(
            query,
            num_results=int(num_results),
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            start_crawl_date=start_crawl_date,
            end_crawl_date=end_crawl_date,
            start_published_date=start_published_date,
            end_published_date=end_published_date,
            include_text=include_text,
            exclude_text=exclude_text,
            use_autoprompt=use_autoprompt,
            type=type,
            category=category,
            flags=flags,
            moderation=moderation,
            text=text,
        )

        results = build_response(search_results)
        # Convert Pydantic models to dictionaries before JSON serialization
        serializable_results = [result.model_dump() for result in results]
        return json.dumps(serializable_results)

    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        return json.dumps({"error": str(e)})


def build_response(results: SearchResponse[ResultWithText]) -> List[ExaSearchResult]:
    """Build search response from Exa results"""
    try:
        return [
            ExaSearchResult(
                id=result_with_text.id,
                title=result_with_text.title or "",
                url=result_with_text.url or "",
                publishedDate=result_with_text.published_date or "",
                author=result_with_text.author or "",
                score=str(result_with_text.score),
                text=result_with_text.text or "",
                image=result_with_text.image or "",
                favicon=result_with_text.favicon or "",
            )
            for result_with_text in results.results
        ]
    except Exception as e:
        logger.error(f"Error building response: {str(e)}")
        return []


if __name__ == "__main__":
    run_mcp_server("Search Server", funcs=[mcpsearch], port=5555)
