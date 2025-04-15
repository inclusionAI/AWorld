"""
Wikipedia MCP Server

This module provides MCP server functionality for interacting with Wikipedia.
It supports searching Wikipedia, retrieving article content, and getting summaries.

Key features:
- Search Wikipedia for articles
- Retrieve full article content
- Get article summaries
- Fetch random articles
- Get article categories and links

Main functions:
- mcpwikisearch: Searches Wikipedia for articles matching a query
- mcpwikicontent: Retrieves the full content of a Wikipedia article
- mcpwikisummary: Gets a summary of a Wikipedia article
- mcpwikirandom: Retrieves random Wikipedia articles
- mcpwikicategories: Gets categories for a Wikipedia article
- mcpwikilinks: Gets links from a Wikipedia article
"""

import traceback
from typing import Dict, List, Optional, Union

import wikipedia
from pydantic import BaseModel, Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server


# Define model classes for Wikipedia operations
class WikipediaSearchResult(BaseModel):
    """Model representing a Wikipedia search result"""

    title: str
    snippet: Optional[str] = None
    url: Optional[str] = None


class WikipediaArticle(BaseModel):
    """Model representing a Wikipedia article"""

    title: str
    pageid: Optional[int] = None
    url: str
    content: str
    summary: str
    images: Optional[List[str]] = None
    categories: Optional[List[str]] = None
    links: Optional[List[str]] = None
    references: Optional[List[str]] = None
    sections: Optional[List[Dict[str, str]]] = None


class WikipediaResponse(BaseModel):
    """Model representing a Wikipedia API response"""

    query: str
    results: Union[
        List[WikipediaSearchResult], List[WikipediaArticle], WikipediaArticle
    ]
    count: int
    language: str
    error: Optional[str] = None


class WikipediaError(BaseModel):
    """Model representing an error in Wikipedia processing"""

    error: str
    operation: str
    query: Optional[str] = None


def handle_error(e: Exception, operation_type: str, query: Optional[str] = None) -> str:
    """Unified error handling and return standard format error message"""
    error_msg = f"{operation_type} error: {str(e)}"
    logger.error(f"Wikipedia {operation_type} operation failed: {str(e)}")
    logger.error(traceback.format_exc())

    error = WikipediaError(error=error_msg, operation=operation_type, query=query)

    return error.model_dump_json()


def mcpwikisearch(
    query: str = Field(..., description="The search query string"),
    limit: int = Field(10, description="Maximum number of results to return"),
    language: str = Field(
        "en", description="Language code for Wikipedia (e.g., 'en', 'es', 'fr')"
    ),
) -> str:
    """
    Search Wikipedia for articles matching the query.

    Args:
        query: Search query string
        limit: Maximum number of results to return
        language: Language code for Wikipedia

    Returns:
        JSON string containing search results
    """
    logger.info(
        f"Performing Wikipedia search for query: '{query}' in language: {language}"
    )
    try:
        # Set Wikipedia language
        wikipedia.set_lang(language)

        # Search Wikipedia
        search_results = wikipedia.search(query, results=limit)

        # Format results
        formatted_results = []
        for title in search_results:
            try:
                # Get a summary to use as a snippet
                summary = wikipedia.summary(title, sentences=1, auto_suggest=False)
                # Create URL
                url = f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}"

                result = WikipediaSearchResult(title=title, snippet=summary, url=url)
                formatted_results.append(result)
            except Exception as e:
                logger.warning(f"Error getting details for '{title}': {str(e)}")
                # Still include the result, but without a snippet
                url = f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}"
                result = WikipediaSearchResult(title=title, url=url)
                formatted_results.append(result)

        # Create response
        response = WikipediaResponse(
            query=query,
            results=formatted_results,
            count=len(formatted_results),
            language=language,
        )

        logger.info(f"Wikipedia search completed with {len(formatted_results)} results")
        return response.model_dump_json()

    except Exception as e:
        return handle_error(e, "Search", query)


def mcpwikicontent(
    title: str = Field(..., description="Title of the Wikipedia article"),
    auto_suggest: bool = Field(
        True, description="Whether to use Wikipedia's auto-suggest feature"
    ),
    redirect: bool = Field(True, description="Whether to follow redirects"),
    language: str = Field(
        "en", description="Language code for Wikipedia (e.g., 'en', 'es', 'fr')"
    ),
) -> str:
    """
    Retrieve the full content of a Wikipedia article.

    Args:
        title: Title of the Wikipedia article
        auto_suggest: Whether to use Wikipedia's auto-suggest feature
        redirect: Whether to follow redirects
        language: Language code for Wikipedia

    Returns:
        JSON string containing the article content
    """
    logger.info(
        f"Retrieving Wikipedia article content for: '{title}' in language: {language}"
    )
    try:
        # Set Wikipedia language
        wikipedia.set_lang(language)

        # Get the page
        page = wikipedia.page(title, auto_suggest=auto_suggest, redirect=redirect)

        # Create article object
        article = WikipediaArticle(
            title=page.title,
            pageid=page.pageid,
            url=page.url,
            content=page.content,
            summary=page.summary,
            images=page.images,
            categories=page.categories,
            links=page.links,
            references=page.references,
            sections=[
                {"title": section, "content": page.section(section)}
                for section in page.sections
            ],
        )

        # Create response
        response = WikipediaResponse(
            query=title, results=article, count=1, language=language
        )

        logger.info(f"Wikipedia article content retrieved successfully for: '{title}'")
        return response.model_dump_json()

    except Exception as e:
        return handle_error(e, "Content Retrieval", title)


def mcpwikisummary(
    title: str = Field(..., description="Title of the Wikipedia article"),
    sentences: int = Field(
        5, description="Number of sentences to return in the summary"
    ),
    auto_suggest: bool = Field(
        True, description="Whether to use Wikipedia's auto-suggest feature"
    ),
    redirect: bool = Field(True, description="Whether to follow redirects"),
    language: str = Field(
        "en", description="Language code for Wikipedia (e.g., 'en', 'es', 'fr')"
    ),
) -> str:
    """
    Get a summary of a Wikipedia article.

    Args:
        title: Title of the Wikipedia article
        sentences: Number of sentences to return in the summary
        auto_suggest: Whether to use Wikipedia's auto-suggest feature
        redirect: Whether to follow redirects
        language: Language code for Wikipedia

    Returns:
        JSON string containing the article summary
    """
    logger.info(f"Retrieving Wikipedia summary for: '{title}' in language: {language}")
    try:
        # Set Wikipedia language
        wikipedia.set_lang(language)

        # Get the summary
        summary = wikipedia.summary(
            title, sentences=sentences, auto_suggest=auto_suggest, redirect=redirect
        )

        # Get the URL
        url = f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}"

        # Create article object with just the summary
        article = WikipediaArticle(
            title=title,
            url=url,
            content="",  # Empty content since we're just getting the summary
            summary=summary,
        )

        # Create response
        response = WikipediaResponse(
            query=title, results=article, count=1, language=language
        )

        logger.info(f"Wikipedia summary retrieved successfully for: '{title}'")
        return response.model_dump_json()

    except Exception as e:
        return handle_error(e, "Summary Retrieval", title)


def mcpwikirandom(
    pages: int = Field(1, description="Number of random articles to retrieve"),
    language: str = Field(
        "en", description="Language code for Wikipedia (e.g., 'en', 'es', 'fr')"
    ),
) -> str:
    """
    Retrieve random Wikipedia articles.

    Args:
        pages: Number of random articles to retrieve
        language: Language code for Wikipedia

    Returns:
        JSON string containing random articles
    """
    logger.info(f"Retrieving {pages} random Wikipedia articles in language: {language}")
    try:
        # Set Wikipedia language
        wikipedia.set_lang(language)

        # Get random titles
        random_titles = wikipedia.random(pages=pages)

        # Handle case where only one title is returned (not in a list)
        if isinstance(random_titles, str):
            random_titles = [random_titles]

        # Format results
        formatted_results = []
        for title in random_titles:
            try:
                # Get a summary
                summary = wikipedia.summary(title, sentences=2, auto_suggest=False)
                # Create URL
                url = f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}"

                result = WikipediaSearchResult(title=title, snippet=summary, url=url)
                formatted_results.append(result)
            except Exception as e:
                logger.warning(
                    f"Error getting details for random article '{title}': {str(e)}"
                )
                # Still include the result, but without a snippet
                url = f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}"
                result = WikipediaSearchResult(title=title, url=url)
                formatted_results.append(result)

        # Create response
        response = WikipediaResponse(
            query="random",
            results=formatted_results,
            count=len(formatted_results),
            language=language,
        )

        logger.info(f"Retrieved {len(formatted_results)} random Wikipedia articles")
        return response.model_dump_json()

    except Exception as e:
        return handle_error(e, "Random Articles Retrieval")


def mcpwikicategories(
    title: str = Field(..., description="Title of the Wikipedia article"),
    language: str = Field(
        "en", description="Language code for Wikipedia (e.g., 'en', 'es', 'fr')"
    ),
) -> str:
    """
    Get categories for a Wikipedia article.

    Args:
        title: Title of the Wikipedia article
        language: Language code for Wikipedia

    Returns:
        JSON string containing the article categories
    """
    logger.info(
        f"Retrieving categories for Wikipedia article: '{title}' in language: {language}"
    )
    try:
        # Set Wikipedia language
        wikipedia.set_lang(language)

        # Get the page
        page = wikipedia.page(title, auto_suggest=True, redirect=True)

        # Create response
        response = WikipediaResponse(
            query=title,
            results=page.categories,
            count=len(page.categories),
            language=language,
        )

        logger.info(
            f"Retrieved {len(page.categories)} categories for Wikipedia article: '{title}'"
        )
        return response.model_dump_json()

    except Exception as e:
        return handle_error(e, "Categories Retrieval", title)


def mcpwikilinks(
    title: str = Field(..., description="Title of the Wikipedia article"),
    language: str = Field(
        "en", description="Language code for Wikipedia (e.g., 'en', 'es', 'fr')"
    ),
) -> str:
    """
    Get links from a Wikipedia article.

    Args:
        title: Title of the Wikipedia article
        language: Language code for Wikipedia

    Returns:
        JSON string containing the article links
    """
    logger.info(
        f"Retrieving links from Wikipedia article: '{title}' in language: {language}"
    )
    try:
        # Set Wikipedia language
        wikipedia.set_lang(language)

        # Get the page
        page = wikipedia.page(title, auto_suggest=True, redirect=True)

        # Format results
        formatted_results = []
        for link_title in page.links:
            try:
                url = f"https://{language}.wikipedia.org/wiki/{link_title.replace(' ', '_')}"
                result = WikipediaSearchResult(title=link_title, url=url)
                formatted_results.append(result)
            except Exception as e:
                logger.warning(f"Error formatting link '{link_title}': {str(e)}")

        # Create response
        response = WikipediaResponse(
            query=title,
            results=formatted_results,
            count=len(formatted_results),
            language=language,
        )

        logger.info(
            f"Retrieved {len(formatted_results)} links from Wikipedia article: '{title}'"
        )
        return response.model_dump_json()

    except Exception as e:
        return handle_error(e, "Links Retrieval", title)


# Main function
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Launch Wikipedia MCP server with port allocation"
    )
    parser.add_argument(
        "--port",
        type=int,
        help="Listening port. Must be specified.",
    )
    args = parser.parse_args()

    logger.info("Starting Wikipedia Server")
    run_mcp_server(
        "Wikipedia Server",
        funcs=[
            mcpwikisearch,
            mcpwikicontent,
            mcpwikisummary,
            mcpwikirandom,
            mcpwikicategories,
            mcpwikilinks,
        ],
        port=args.port,
    )
