"""
Search MCP Server

This module provides MCP server functionality for performing web searches using various search engines.
It supports structured queries and returns formatted search results.

Key features:
- Perform web searches using Exa, Google, and DuckDuckGo
- Filter and format search results
- Validate and process search queries

Main functions:
- mcpsearchexa: Searches the web using Exa
- mcpsearchgoogle: Searches the web using Google
- mcpsearchduckduckgo: Searches the web using DuckDuckGo
"""

import os
import re
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from exa_py import Exa
from exa_py.api import SearchResponse
from pydantic import BaseModel, Field, field_validator

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server


# Base search result model that all providers will use
class SearchResult(BaseModel):
    """Base search result model with common fields"""

    id: str
    title: str
    url: str
    snippet: str
    source: str  # Which search engine provided this result

    @field_validator("url")
    def validate_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("Invalid URL format")
        return v


class ExaSearchResult(SearchResult):
    """Exa-specific search result model"""

    publishedDate: str = ""
    author: str = ""
    score: str = ""
    text: str = ""
    image: str = ""
    favicon: str = ""


class GoogleSearchResult(SearchResult):
    """Google-specific search result model"""

    displayLink: str = ""
    formattedUrl: str = ""
    htmlSnippet: str = ""
    htmlTitle: str = ""
    kind: str = ""
    link: str = ""


class DuckDuckGoSearchResult(SearchResult):
    """DuckDuckGo-specific search result model"""

    description: str = ""
    icon: str = ""
    published: str = ""


class SearchResponse(BaseModel):
    """Unified search response model"""

    query: str
    results: List[SearchResult]
    count: int
    source: str
    error: Optional[str] = None


def mcpsearchexa(
    query: str = Field(..., description="The query string."),
    num_results: int = Field(
        20, description="Number of search results to return (default 20)."
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
) -> str:
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

        logger.info(f"Exa search starts for query: {query}")
        search_results = [
            ExaSearchResult(
                id=result_with_text.id,
                title=result_with_text.title or "",
                url=result_with_text.url or "",
                snippet=(
                    result_with_text.text[:100] + "..." if result_with_text.text else ""
                ),
                source="exa",
                publishedDate=result_with_text.published_date or "",
                author=result_with_text.author or "",
                score=str(result_with_text.score),
                text=result_with_text.text or "",
                image=result_with_text.image or "",
                favicon=result_with_text.favicon or "",
            )
            for result_with_text in exa.search_and_contents(
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
                text=text,
            )
        ]
        search_response = SearchResponse(
            query=query, results=search_results, count=len(search_results), source="exa"
        )
        return search_response.model_dump_json()

    except Exception as e:
        logger.error(f"Exa search error: {str(e)}")
        return SearchResponse(
            query=query, results=[], count=0, source="exa", error=str(e)
        ).model_dump_json()


def mcpsearchgoogle(
    query: str = Field(..., description="The search query string."),
    num_results: int = Field(
        10, description="Number of search results to return (default 10)."
    ),
    safe_search: bool = Field(
        True, description="Whether to enable safe search filtering."
    ),
    language: str = Field("en", description="Language code for search results."),
    country: str = Field("us", description="Country code for search results."),
) -> str:
    """
    Search the web using Google Custom Search API.

    Requires GOOGLE_API_KEY and GOOGLE_CSE_ID environment variables to be set.
    """
    try:
        api_key = os.environ.get("GOOGLE_API_KEY")
        cse_id = os.environ.get("GOOGLE_CSE_ID")

        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable not set")
        if not cse_id:
            raise ValueError("GOOGLE_CSE_ID environment variable not set")

        # Ensure num_results is within valid range
        num_results = max(1, num_results)

        # Build the Google Custom Search API URL
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": api_key,
            "cx": cse_id,
            "q": query,
            "num": num_results,
            "safe": "active" if safe_search else "off",
            "hl": language,
            "gl": country,
        }

        logger.info(f"Google search starts for query: {query}")
        response = requests.get(url, params=params)
        response.raise_for_status()

        data = response.json()
        search_results = []

        if "items" in data:
            for i, item in enumerate(data["items"]):
                result = GoogleSearchResult(
                    id=f"google-{i}",
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    source="google",
                    displayLink=item.get("displayLink", ""),
                    formattedUrl=item.get("formattedUrl", ""),
                    htmlSnippet=item.get("htmlSnippet", ""),
                    htmlTitle=item.get("htmlTitle", ""),
                    kind=item.get("kind", ""),
                    link=item.get("link", ""),
                )
                search_results.append(result)

        return SearchResponse(
            query=query,
            results=search_results,
            count=len(search_results),
            source="google",
        ).model_dump_json()

    except Exception as e:
        logger.error(f"Google search error: {str(e)}")
        return SearchResponse(
            query=query, results=[], count=0, source="google", error=str(e)
        ).model_dump_json()


def mcpsearchduckduckgo(
    query: str = Field(..., description="The search query string."),
    num_results: int = Field(
        20, description="Number of search results to return (default 20)."
    ),
    region: str = Field(
        "wt-wt", description="Region code for search results (default: wt-wt)."
    ),
    safe_search: bool = Field(
        True, description="Whether to enable safe search filtering."
    ),
    time_period: Optional[str] = Field(
        None, description="Time period for results (d, w, m, y)."
    ),
) -> str:
    """
    Search the web using DuckDuckGo API.

    This uses the DuckDuckGo HTML search page and parses the results.
    """
    try:
        # Build the DuckDuckGo search URL
        url = "https://html.duckduckgo.com/html/"

        # Prepare parameters
        params = {
            "q": query,
            "kl": region,  # Region/locale
            "kp": "1" if safe_search else "-1",  # Safe search (1 = on, -1 = off)
        }

        # Add time period if specified
        if time_period:
            if time_period in ["d", "w", "m", "y"]:
                params["df"] = time_period
            else:
                logger.warning(f"Invalid time period: {time_period}. Using default.")

        # Set headers to mimic a browser
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://duckduckgo.com/",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
        }

        logger.info(f"DuckDuckGo search starts for query: {query}")
        response = requests.post(url, data=params, headers=headers)
        response.raise_for_status()

        # Parse the HTML response
        soup = BeautifulSoup(response.text, "html.parser")
        results_elements = soup.select(".result")

        search_results = []
        for i, result in enumerate(results_elements[:num_results]):
            # Extract result information
            title_element = result.select_one(".result__a")
            snippet_element = result.select_one(".result__snippet")
            url_element = result.select_one(".result__url")

            if title_element and url_element:
                title = title_element.get_text(strip=True)
                raw_url = title_element.get("href", "")

                # Extract the actual URL from DuckDuckGo's redirect URL
                url = raw_url
                if raw_url.startswith("/"):
                    url_match = re.search(r"uddg=([^&]+)", raw_url)
                    if url_match:
                        url = requests.utils.unquote(url_match.group(1))

                snippet = (
                    snippet_element.get_text(strip=True) if snippet_element else ""
                )
                display_url = url_element.get_text(strip=True) if url_element else ""

                result = DuckDuckGoSearchResult(
                    id=f"ddg-{i}",
                    title=title,
                    url=url,
                    snippet=snippet,
                    source="duckduckgo",
                    description=snippet,
                    icon="",
                    published="",
                )
                search_results.append(result)

        return SearchResponse(
            query=query,
            results=search_results,
            count=len(search_results),
            source="duckduckgo",
        ).model_dump_json()

    except Exception as e:
        logger.error(f"DuckDuckGo search error: {str(e)}")
        return SearchResponse(
            query=query, results=[], count=0, source="duckduckgo", error=str(e)
        ).model_dump_json()


if __name__ == "__main__":
    run_mcp_server(
        "Search Server",
        funcs=[mcpsearchgoogle, mcpsearchduckduckgo, mcpsearchexa],
        port=2010,
    )
