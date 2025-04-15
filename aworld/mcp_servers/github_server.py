"""
GitHub MCP Server

This module provides MCP server functionality for interacting with the GitHub API.
It allows for retrieving repositories, issues, pull requests, and user information from GitHub.

Key features:
- Retrieve repository information
- Fetch file contents from repositories
- Search for repositories, code, issues
- Get user information and repositories
- List repository contents

Main functions:
- mcpgetrepository: Retrieves information about a repository
- mcpgetfilecontent: Fetches content of a file from a repository
- mcpsearchrepositories: Searches for repositories based on query
- mcpgetuser: Retrieves user information
"""

import base64
import json
import os
import traceback
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server
from aworld.utils import import_package

# Import PyGithub package, install if not available
import_package("github", install_name="PyGithub")

from github import Github, GithubException, Label
from github.ContentFile import ContentFile
from github.Repository import Repository


# Define model classes for different GitHub API responses
class GitHubRepository(BaseModel):
    """Model representing a GitHub repository"""

    id: int
    name: str
    full_name: str
    owner: str
    description: str = ""
    html_url: str
    stars: int
    forks: int
    watchers: int
    language: Optional[str] = None
    default_branch: str
    open_issues_count: int
    created_at: str
    updated_at: str
    topics: List[str] = []
    is_private: bool
    is_fork: bool
    has_wiki: bool
    has_issues: bool


class GitHubFile(BaseModel):
    """Model representing a file in a GitHub repository"""

    name: str
    path: str
    sha: str
    size: int
    url: str
    html_url: str
    type: str  # "file" or "dir"
    content: Optional[str] = None
    encoding: Optional[str] = None


class GitHubUser(BaseModel):
    """Model representing a GitHub user"""

    id: int
    login: str
    name: Optional[str] = None
    avatar_url: str
    html_url: str
    type: str
    public_repos: int
    followers: int
    following: int
    created_at: str
    updated_at: str
    bio: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    email: Optional[str] = None
    blog: Optional[str] = None


class GitHubIssue(BaseModel):
    """Model representing a GitHub issue"""

    id: int
    number: int
    title: str
    body: str = ""
    state: str
    html_url: str
    user: str
    created_at: str
    updated_at: str
    closed_at: Optional[str] = None
    labels: List[str] = []
    assignees: List[str] = []
    comments: int
    is_pull_request: bool = False


class GitHubSearchResult(BaseModel):
    """Model representing search results"""

    query: str
    total_count: int
    items: List[Any]


class GitHubError(BaseModel):
    """Model representing an error in GitHub API processing"""

    error: str
    operation: str


def handle_error(e: Exception, operation_type: str) -> str:
    """Unified error handling and return standard format error message"""
    error_msg = f"{operation_type} error: {str(e)}"
    logger.error(traceback.format_exc())

    error = GitHubError(error=error_msg, operation=operation_type)

    return error.model_dump_json()


def get_github_instance() -> Github:
    """
    Create and return a GitHub API instance.

    Returns:
        Authenticated GitHub instance
    """
    token = os.environ.get("GITHUB_ACCESS_TOKEN")
    if token:
        return Github(token)
    else:
        logger.warning(
            "No GitHub token found. Using unauthenticated access with rate limits."
        )
        return Github()


def mcpgetrepository(
    repo_full_name: str = Field(description="Repository full name (owner/repo)"),
) -> str:
    """
    Get information about a GitHub repository.

    Args:
        repo_full_name: Repository full name in format "owner/repo"

    Returns:
        JSON string containing repository information
    """
    try:
        # Initialize GitHub API
        github = get_github_instance()

        # Get repository
        repo = github.get_repo(repo_full_name)

        # Create repository object
        repo_obj = GitHubRepository(
            id=repo.id,
            name=repo.name,
            full_name=repo.full_name,
            owner=repo.owner.login,
            description=repo.description or "",
            html_url=repo.html_url,
            stars=repo.stargazers_count,
            forks=repo.forks_count,
            watchers=repo.watchers_count,
            language=repo.language,
            default_branch=repo.default_branch,
            open_issues_count=repo.open_issues_count,
            created_at=repo.created_at.isoformat(),
            updated_at=repo.updated_at.isoformat(),
            topics=repo.get_topics(),
            is_private=repo.private,
            is_fork=repo.fork,
            has_wiki=repo.has_wiki,
            has_issues=repo.has_issues,
        )

        return repo_obj.model_dump_json()

    except Exception as e:
        return handle_error(e, "Get Repository")


def mcplistrepositorycontents(
    repo_full_name: str = Field(description="Repository full name (owner/repo)"),
    path: str = Field(default="", description="Directory path within repository"),
    ref: str = Field(default="", description="Branch, tag, or commit SHA (optional)"),
) -> str:
    """
    List contents of a directory in a GitHub repository.

    Args:
        repo_full_name: Repository full name in format "owner/repo"
        path: Directory path within repository (default: root)
        ref: Branch, tag, or commit SHA (optional)

    Returns:
        JSON string containing directory contents
    """
    try:
        # Initialize GitHub API
        github = get_github_instance()

        # Get repository
        repo = github.get_repo(repo_full_name)

        # Get directory contents
        if ref:
            contents = repo.get_contents(path, ref=ref)
        else:
            contents = repo.get_contents(path)

        # Handle file case
        if not isinstance(contents, list):
            raise ValueError(f"Path {path} is a file, not a directory")

        # Process contents
        items = []
        for item in contents:
            file_obj = GitHubFile(
                name=item.name,
                path=item.path,
                sha=item.sha,
                size=item.size,
                url=item.url,
                html_url=item.html_url,
                type="file" if item.type == "file" else "dir",
            )
            items.append(file_obj)

        # Create result
        result = {
            "repository": repo_full_name,
            "path": path,
            "contents": [item.model_dump() for item in items],
            "count": len(items),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "List Repository Contents")


def mcpsearchrepositories(
    query: str = Field(description="Search query"),
    sort: str = Field(
        default="stars",
        description="Sort method (stars, forks, updated, help-wanted-issues)",
    ),
    order: str = Field(default="desc", description="Sort order (asc, desc)"),
    limit: int = Field(
        default=30, description="Number of results to retrieve (max 100)"
    ),
) -> str:
    """
    Search for GitHub repositories based on query.

    Args:
        query: Search query
        sort: Sort method for results
        order: Sort order (asc, desc)
        limit: Number of results to retrieve (max 100)

    Returns:
        JSON string containing search results
    """
    try:
        # Validate input
        if limit > 100:
            limit = 100
            logger.warning("Limit capped at 100 results")

        # Initialize GitHub API
        github = get_github_instance()

        # Search repositories
        repositories = github.search_repositories(query=query, sort=sort, order=order)

        # Process results
        repos = []
        count = 0
        for repo in repositories:
            if count >= limit:
                break

            repo_obj = GitHubRepository(
                id=repo.id,
                name=repo.name,
                full_name=repo.full_name,
                owner=repo.owner.login,
                description=repo.description or "",
                html_url=repo.html_url,
                stars=repo.stargazers_count,
                forks=repo.forks_count,
                watchers=repo.watchers_count,
                language=repo.language,
                default_branch=repo.default_branch,
                open_issues_count=repo.open_issues_count,
                created_at=repo.created_at.isoformat(),
                updated_at=repo.updated_at.isoformat(),
                topics=repo.get_topics(),
                is_private=repo.private,
                is_fork=repo.fork,
                has_wiki=repo.has_wiki,
                has_issues=repo.has_issues,
            )
            repos.append(repo_obj)
            count += 1

        # Create search result
        search_result = GitHubSearchResult(
            query=query,
            total_count=repositories.totalCount,
            items=[repo.model_dump() for repo in repos],
        )

        return search_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Search Repositories")


def mcpsearchcode(
    query: str = Field(description="Search query"),
    language: str = Field(default="", description="Filter by language (optional)"),
    repo: str = Field(default="", description="Filter by repository (optional)"),
    limit: int = Field(
        default=30, description="Number of results to retrieve (max 100)"
    ),
) -> str:
    """
    Search for code in GitHub repositories.

    Args:
        query: Search query
        language: Filter by language (optional)
        repo: Filter by repository in format "owner/repo" (optional)
        limit: Number of results to retrieve (max 100)

    Returns:
        JSON string containing search results
    """
    try:
        # Validate input
        if limit > 100:
            limit = 100
            logger.warning("Limit capped at 100 results")

        # Build search query
        search_query = query
        if language:
            search_query += f" language:{language}"
        if repo:
            search_query += f" repo:{repo}"

        # Initialize GitHub API
        github = get_github_instance()

        # Search code
        code_results = github.search_code(query=search_query)

        # Process results
        code_items = []
        count = 0
        for code in code_results:
            if count >= limit:
                break

            code_item = {
                "name": code.name,
                "path": code.path,
                "sha": code.sha,
                "url": code.url,
                "html_url": code.html_url,
                "repository": code.repository.full_name,
                "score": code.score,
            }
            code_items.append(code_item)
            count += 1

        # Create search result
        result = {
            "query": search_query,
            "total_count": code_results.totalCount,
            "items": code_items,
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Search Code")


def mcpgetuser(
    username: str = Field(description="GitHub username"),
) -> str:
    """
    Get information about a GitHub user.

    Args:
        username: GitHub username

    Returns:
        JSON string containing user information
    """
    try:
        # Initialize GitHub API
        github = get_github_instance()

        # Get user
        user = github.get_user(username)

        # Create user object
        user_obj = GitHubUser(
            id=user.id,
            login=user.login,
            name=user.name,
            avatar_url=user.avatar_url,
            html_url=user.html_url,
            type=user.type,
            public_repos=user.public_repos,
            followers=user.followers,
            following=user.following,
            created_at=user.created_at.isoformat(),
            updated_at=user.updated_at.isoformat(),
            bio=user.bio,
            company=user.company,
            location=user.location,
            email=user.email,
            blog=user.blog,
        )

        return user_obj.model_dump_json()

    except Exception as e:
        return handle_error(e, "Get User")


def mcpgetuserrepositories(
    username: str = Field(description="GitHub username"),
    sort: str = Field(
        default="updated",
        description="Sort method (created, updated, pushed, full_name)",
    ),
    direction: str = Field(default="desc", description="Sort direction (asc, desc)"),
    limit: int = Field(
        default=30, description="Number of repositories to retrieve (max 100)"
    ),
) -> str:
    """
    Get repositories owned by a GitHub user.

    Args:
        username: GitHub username
        sort: Sort method for repositories
        direction: Sort direction (asc, desc)
        limit: Number of repositories to retrieve (max 100)

    Returns:
        JSON string containing user repositories
    """
    try:
        # Validate input
        if limit > 100:
            limit = 100
            logger.warning("Limit capped at 100 repositories")

        # Initialize GitHub API
        github = get_github_instance()

        # Get user
        user = github.get_user(username)

        # Get user repositories
        repositories = user.get_repos(sort=sort, direction=direction)

        # Process repositories
        repos = []
        count = 0
        for repo in repositories:
            if count >= limit:
                break

            repo_obj = GitHubRepository(
                id=repo.id,
                name=repo.name,
                full_name=repo.full_name,
                owner=repo.owner.login,
                description=repo.description or "",
                html_url=repo.html_url,
                stars=repo.stargazers_count,
                forks=repo.forks_count,
                watchers=repo.watchers_count,
                language=repo.language,
                default_branch=repo.default_branch,
                open_issues_count=repo.open_issues_count,
                created_at=repo.created_at.isoformat(),
                updated_at=repo.updated_at.isoformat(),
                topics=repo.get_topics(),
                is_private=repo.private,
                is_fork=repo.fork,
                has_wiki=repo.has_wiki,
                has_issues=repo.has_issues,
            )
            repos.append(repo_obj)
            count += 1

        # Create result
        result = {
            "username": username,
            "repositories": [repo.model_dump() for repo in repos],
            "count": len(repos),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get User Repositories")


def mcpgetissues(
    repo_full_name: str = Field(description="Repository full name (owner/repo)"),
    state: str = Field(default="open", description="Issue state (open, closed)"),
    filter_labels: List[Label] = Field(
        default=[],
        description="Filter issues by labels, apply `and` logic for multiple labels",
    ),
    sort: str = Field(
        default="created", description="Sort method (created, updated, comments)"
    ),
    direction: str = Field(default="desc", description="Sort direction (asc, desc)"),
    limit: int = Field(
        default=375, description="Number of issues to retrieve (max 1024)"
    ),
) -> str:
    """
    Get issues from a GitHub repository.

    Args:
        repo_full_name: Repository full name in format "owner/repo"
        state: Issue state (open, closed)
        sort: Sort method for issues
        direction: Sort direction (asc, desc)
        limit: Number of issues to retrieve (max 1024)
        filter_labels: Filter issues by labels, always validate labels existing before passing

    Returns:
        JSON string containing repository issues
    """
    try:
        # Validate input
        if limit > 1024:
            limit = 1024
            logger.warning("Limit capped at 1024 issues")

        # Initialize GitHub API
        github = get_github_instance()

        # Get repository
        repo = github.get_repo(repo_full_name)

        # Get issues
        issues = repo.get_issues(
            state=state, sort=sort, direction=direction, labels=filter_labels
        )

        # Process issues
        issue_list = []
        count = 0

        for issue in issues:
            if count >= limit:
                break

            # Skip pull requests if they're returned with issues
            is_pull_request = issue.pull_request is not None

            issue_obj = GitHubIssue(
                id=issue.id,
                number=issue.number,
                title=issue.title,
                body=issue.body or "",
                state=issue.state,
                html_url=issue.html_url,
                user=issue.user.login,
                created_at=issue.created_at.isoformat(),
                updated_at=issue.updated_at.isoformat(),
                closed_at=issue.closed_at.isoformat() if issue.closed_at else None,
                labels=[label.name for label in issue.labels],
                assignees=[assignee.login for assignee in issue.assignees],
                comments=issue.comments,
                is_pull_request=is_pull_request,
            )
            issue_list.append(issue_obj)
            count += 1

        # Create result
        result = {
            "repository": repo_full_name,
            "issues": [issue.model_dump() for issue in issue_list],
            "count": len(issue_list),
            "filter_applied": len(filter_labels) > 0,
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get Issues")


def mcpgetlabels(
    repo_full_name: str = Field(description="Repository full name (owner/repo)"),
) -> str:
    """
    Get all labels from a GitHub repository.

    Args:
        repo_full_name: Repository full name in format "owner/repo"

    Returns:
        JSON string containing repository labels
    """
    try:
        # Initialize GitHub API
        github = get_github_instance()

        # Get repository
        repo = github.get_repo(repo_full_name)

        # Get labels
        labels = repo.get_labels()

        # Process labels
        label_list = []
        for label in labels:
            label_obj = {
                "name": label.name,
                "description": label.description or "",
                "color": label.color,
                "url": label.url,
            }
            label_list.append(label_obj)

        # Create result
        result = {
            "repository": repo_full_name,
            "labels": label_list,
            "count": len(label_list),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get Labels")


# Main function
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Launch MCP servers with random port allocation"
    )
    parser.add_argument(
        "--port",
        type=int,
        help=f"Listening to port. Must be specified.",
    )
    args = parser.parse_args()
    run_mcp_server(
        "GitHub Server",
        funcs=[
            mcpgetrepository,
            mcpsearchrepositories,
            mcpsearchcode,
            mcpgetuser,
            mcpgetuserrepositories,
            mcpgetlabels,
            mcpgetissues,
        ],
        port=args.port,
    )
