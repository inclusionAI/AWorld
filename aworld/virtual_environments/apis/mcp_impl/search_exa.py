import json
import os
from typing import List, Optional

from exa_py import Exa
from exa_py.api import ResultWithText, SearchResponse
from pydantic import BaseModel, Field, field_validator

from aworld.logs.util import logger
from aworld.virtual_environments.toolagents.mcp_impl.utils import run_mcp_server


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
        json_encoders = {
            "publishedDate": lambda v: (
                v.isoformat() if hasattr(v, "isoformat") else str(v)
            )
        }


def mcpsearch(
    query: str = Field(..., description="The query string."),
    *,
    num_results: Optional[int] = Field(
        20, description="Number of search results to return (default 10)."
    ),
    include_domains: Optional[List[str]] = Field(
        None, description="Domains to include in the search."
    ),
    exclude_domains: Optional[List[str]] = Field(
        None, description="Domains to exclude from the search."
    ),
    start_crawl_date: Optional[str] = Field(
        None, description="Only links crawled after this date."
    ),
    end_crawl_date: Optional[str] = Field(
        None, description="Only links crawled before this date."
    ),
    start_published_date: Optional[str] = Field(
        None, description="Only links published after this date."
    ),
    end_published_date: Optional[str] = Field(
        None, description="Only links published before this date."
    ),
    include_text: Optional[List[str]] = Field(
        None, description="Strings that must appear in the page text."
    ),
    exclude_text: Optional[List[str]] = Field(
        None, description="Strings that must not appear in the page text."
    ),
    use_autoprompt: Optional[bool] = Field(
        False, description="Convert query to Exa (default False)."
    ),
    type: Optional[str] = Field(
        "neural", description="'keyword' or 'neural' (default 'neural')."
    ),
    category: Optional[str] = Field(None, description="e.g. 'company'"),
    flags: Optional[List[str]] = Field(
        None, description="Experimental flags for Exa usage."
    ),
    moderation: Optional[bool] = Field(
        False, description="If True, the search results will be moderated for safety."
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
        search_results = exa.search_and_contents(
            query,
            num_results=num_results,
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
        )

        results = build_response(search_results)
        return [result.model_dump_json() for result in results]

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
