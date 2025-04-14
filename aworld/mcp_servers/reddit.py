"""
Reddit MCP Server

This module provides MCP server functionality for interacting with the Reddit API.
It allows for retrieving posts, comments, and user information from Reddit.

Key features:
- Retrieve hot posts from subreddits
- Fetch comments and user details
- Search for posts based on queries

Main functions:
- mcpgethotposts: Retrieves hot posts from a subreddit
- mcpgetcomments: Fetches comments for a post
- mcpgetuser: Retrieves user information
"""

import json
from typing import List, Optional

from pydantic import BaseModel, Field

from aworld.logs.util import logger
from aworld.mcp_servers.utils import run_mcp_server
from aworld.utils import import_package

# Import PRAW package, install if not available
import_package("praw")
import praw


# Define model classes for different Reddit API responses
class RedditPost(BaseModel):
    """Model representing a Reddit post"""

    id: str
    title: str
    author: str
    subreddit: str
    selftext: str = ""
    url: str
    score: int
    upvote_ratio: float
    num_comments: int
    created_utc: float
    is_self: bool
    permalink: str
    is_video: bool = False
    over_18: bool = False
    stickied: bool = False
    spoiler: bool = False


class RedditComment(BaseModel):
    """Model representing a Reddit comment"""

    id: str
    author: str
    body: str
    score: int
    created_utc: float
    permalink: str
    is_submitter: bool = False
    stickied: bool = False
    replies: List[str] = []  # List of reply IDs


class RedditSubreddit(BaseModel):
    """Model representing a subreddit"""

    id: str
    display_name: str
    title: str
    description: str
    subscribers: int
    created_utc: float
    over18: bool = False
    public_description: str = ""
    url: str


class RedditUser(BaseModel):
    """Model representing a Reddit user"""

    id: str
    name: str
    comment_karma: int
    link_karma: int
    created_utc: float
    is_gold: bool = False
    is_mod: bool = False


class RedditSearchResult(BaseModel):
    """Model representing search results"""

    query: str
    posts: List[RedditPost]
    count: int


class RedditError(BaseModel):
    """Model representing an error in Reddit API processing"""

    error: str
    operation: str


def handle_error(e: Exception, operation_type: str) -> str:
    """Unified error handling and return standard format error message"""
    error_msg = f"{operation_type} error: {str(e)}"
    logger.error(error_msg)

    error = RedditError(error=error_msg, operation=operation_type)

    return error.model_dump_json()


def get_reddit_instance(
    client_id: str,
    client_secret: str,
    user_agent: str = "AWorld Reddit MCP Server",
) -> praw.Reddit:
    """
    Create and return a Reddit API instance.

    Args:
        client_id: Reddit API client ID
        client_secret: Reddit API client secret
        user_agent: User agent string for API requests

    Returns:
        Authenticated Reddit instance
    """
    return praw.Reddit(
        client_id=client_id, client_secret=client_secret, user_agent=user_agent
    )


def mcpgethotposts(
    client_id: str = Field(description="Reddit API client ID"),
    client_secret: str = Field(description="Reddit API client secret"),
    subreddit: str = Field(description="Subreddit name (without r/)"),
    limit: int = Field(default=10, description="Number of posts to retrieve (max 100)"),
    time_filter: str = Field(
        default="day", description="Time filter (hour, day, week, month, year, all)"
    ),
) -> str:
    """
    Get hot posts from a subreddit.

    Args:
        client_id: Reddit API client ID
        client_secret: Reddit API client secret
        subreddit: Subreddit name (without r/)
        limit: Number of posts to retrieve (max 100)
        time_filter: Time filter for posts

    Returns:
        JSON string containing hot posts
    """
    try:
        # Validate input
        if limit > 100:
            limit = 100
            logger.warning("Limit capped at 100 posts")

        # Initialize Reddit API
        reddit = get_reddit_instance(client_id, client_secret)

        # Get subreddit
        subreddit_obj = reddit.subreddit(subreddit)

        # Get hot posts
        posts = []
        for post in subreddit_obj.hot(limit=limit):
            post_obj = RedditPost(
                id=post.id,
                title=post.title,
                author=post.author.name if post.author else "[deleted]",
                subreddit=post.subreddit.display_name,
                selftext=post.selftext,
                url=post.url,
                score=post.score,
                upvote_ratio=post.upvote_ratio,
                num_comments=post.num_comments,
                created_utc=post.created_utc,
                is_self=post.is_self,
                permalink=f"https://www.reddit.com{post.permalink}",
                is_video=post.is_video,
                over_18=post.over_18,
                stickied=post.stickied,
                spoiler=post.spoiler if hasattr(post, "spoiler") else False,
            )
            posts.append(post_obj)

        # Create result
        result = {
            "subreddit": subreddit,
            "posts": [post.model_dump() for post in posts],
            "count": len(posts),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get Hot Posts")


def mcpsearchreddit(
    client_id: str = Field(description="Reddit API client ID"),
    client_secret: str = Field(description="Reddit API client secret"),
    query: str = Field(description="Search query"),
    subreddit: Optional[str] = Field(
        default=None, description="Limit search to specific subreddit (optional)"
    ),
    sort: str = Field(
        default="relevance",
        description="Sort method (relevance, hot, new, top, comments)",
    ),
    time_filter: str = Field(
        default="all", description="Time filter (hour, day, week, month, year, all)"
    ),
    limit: int = Field(
        default=25, description="Number of results to retrieve (max 100)"
    ),
) -> str:
    """
    Search Reddit for posts matching a query.

    Args:
        client_id: Reddit API client ID
        client_secret: Reddit API client secret
        query: Search query
        subreddit: Limit search to specific subreddit (optional)
        sort: Sort method for results
        time_filter: Time filter for results
        limit: Number of results to retrieve (max 100)

    Returns:
        JSON string containing search results
    """
    try:
        # Validate input
        if limit > 100:
            limit = 100
            logger.warning("Limit capped at 100 results")

        # Initialize Reddit API
        reddit = get_reddit_instance(client_id, client_secret)

        # Perform search
        if subreddit:
            search_results = reddit.subreddit(subreddit).search(
                query, sort=sort, time_filter=time_filter, limit=limit
            )
        else:
            search_results = reddit.subreddit("all").search(
                query, sort=sort, time_filter=time_filter, limit=limit
            )

        # Process results
        posts = []
        for post in search_results:
            post_obj = RedditPost(
                id=post.id,
                title=post.title,
                author=post.author.name if post.author else "[deleted]",
                subreddit=post.subreddit.display_name,
                selftext=post.selftext,
                url=post.url,
                score=post.score,
                upvote_ratio=post.upvote_ratio,
                num_comments=post.num_comments,
                created_utc=post.created_utc,
                is_self=post.is_self,
                permalink=f"https://www.reddit.com{post.permalink}",
                is_video=post.is_video,
                over_18=post.over_18,
                stickied=post.stickied,
                spoiler=post.spoiler if hasattr(post, "spoiler") else False,
            )
            posts.append(post_obj)

        # Create result
        search_result = RedditSearchResult(query=query, posts=posts, count=len(posts))

        return search_result.model_dump_json()

    except Exception as e:
        return handle_error(e, "Search Reddit")


def mcpgetpostcomments(
    client_id: str = Field(description="Reddit API client ID"),
    client_secret: str = Field(description="Reddit API client secret"),
    post_id: str = Field(description="Reddit post ID"),
    limit: int = Field(
        default=25, description="Number of comments to retrieve (max 100)"
    ),
    sort: str = Field(
        default="top",
        description="Sort method (top, best, new, controversial, old, qa)",
    ),
) -> str:
    """
    Get comments for a specific Reddit post.

    Args:
        client_id: Reddit API client ID
        client_secret: Reddit API client secret
        post_id: Reddit post ID
        limit: Number of comments to retrieve (max 100)
        sort: Sort method for comments

    Returns:
        JSON string containing post comments
    """
    try:
        # Validate input
        if limit > 100:
            limit = 100
            logger.warning("Limit capped at 100 comments")

        # Initialize Reddit API
        reddit = get_reddit_instance(client_id, client_secret)

        # Get submission
        submission = reddit.submission(id=post_id)

        # Replace more comments
        submission.comment_sort = sort
        submission.comments.replace_more(limit=0)

        # Process comments
        comments = []
        comment_count = 0

        def process_comment(comment, depth=0):
            nonlocal comment_count
            if comment_count >= limit:
                return None

            # Create comment object
            comment_obj = RedditComment(
                id=comment.id,
                author=comment.author.name if comment.author else "[deleted]",
                body=comment.body,
                score=comment.score,
                created_utc=comment.created_utc,
                permalink=f"https://www.reddit.com{comment.permalink}",
                is_submitter=comment.is_submitter,
                stickied=comment.stickied,
                replies=[],
            )

            comment_count += 1

            # Process replies (up to a reasonable depth)
            if depth < 3:  # Limit depth to avoid excessive nesting
                for reply in comment.replies:
                    reply_id = process_comment(reply, depth + 1)
                    if reply_id:
                        comment_obj.replies.append(reply_id)

            comments.append(comment_obj)
            return comment.id

        # Process top-level comments
        for comment in submission.comments:
            if comment_count >= limit:
                break
            process_comment(comment)

        # Create result
        result = {
            "post_id": post_id,
            "post_title": submission.title,
            "comments": [comment.model_dump() for comment in comments],
            "comment_count": len(comments),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get Post Comments")


def mcpgetsubredditinfo(
    client_id: str = Field(description="Reddit API client ID"),
    client_secret: str = Field(description="Reddit API client secret"),
    subreddit: str = Field(description="Subreddit name (without r/)"),
) -> str:
    """
    Get information about a subreddit.

    Args:
        client_id: Reddit API client ID
        client_secret: Reddit API client secret
        subreddit: Subreddit name (without r/)

    Returns:
        JSON string containing subreddit information
    """
    try:
        # Initialize Reddit API
        reddit = get_reddit_instance(client_id, client_secret)

        # Get subreddit
        subreddit_obj = reddit.subreddit(subreddit)

        # Create subreddit object
        subreddit_info = RedditSubreddit(
            id=subreddit_obj.id,
            display_name=subreddit_obj.display_name,
            title=subreddit_obj.title,
            description=subreddit_obj.description,
            subscribers=subreddit_obj.subscribers,
            created_utc=subreddit_obj.created_utc,
            over18=subreddit_obj.over18,
            public_description=subreddit_obj.public_description,
            url=f"https://www.reddit.com{subreddit_obj.url}",
        )

        return subreddit_info.model_dump_json()

    except Exception as e:
        return handle_error(e, "Get Subreddit Info")


def mcpgetuserinfo(
    client_id: str = Field(description="Reddit API client ID"),
    client_secret: str = Field(description="Reddit API client secret"),
    username: str = Field(description="Reddit username"),
) -> str:
    """
    Get information about a Reddit user.

    Args:
        client_id: Reddit API client ID
        client_secret: Reddit API client secret
        username: Reddit username

    Returns:
        JSON string containing user information
    """
    try:
        # Initialize Reddit API
        reddit = get_reddit_instance(client_id, client_secret)

        # Get user
        user = reddit.redditor(username)

        # Create user object
        user_info = RedditUser(
            id=user.id,
            name=user.name,
            comment_karma=user.comment_karma,
            link_karma=user.link_karma,
            created_utc=user.created_utc,
            is_gold=user.is_gold,
            is_mod=user.is_mod,
        )

        return user_info.model_dump_json()

    except Exception as e:
        return handle_error(e, "Get User Info")


def mcpgetuserposts(
    client_id: str = Field(description="Reddit API client ID"),
    client_secret: str = Field(description="Reddit API client secret"),
    username: str = Field(description="Reddit username"),
    limit: int = Field(default=25, description="Number of posts to retrieve (max 100)"),
    sort: str = Field(
        default="new", description="Sort method (new, hot, top, controversial)"
    ),
    time_filter: str = Field(
        default="all", description="Time filter (hour, day, week, month, year, all)"
    ),
) -> str:
    """
    Get posts submitted by a Reddit user.

    Args:
        client_id: Reddit API client ID
        client_secret: Reddit API client secret
        username: Reddit username
        limit: Number of posts to retrieve (max 100)
        sort: Sort method for posts
        time_filter: Time filter for posts

    Returns:
        JSON string containing user posts
    """
    try:
        # Validate input
        if limit > 100:
            limit = 100
            logger.warning("Limit capped at 100 posts")

        # Initialize Reddit API
        reddit = get_reddit_instance(client_id, client_secret)

        # Get user
        user = reddit.redditor(username)

        # Get user posts based on sort method
        if sort == "new":
            submissions = user.submissions.new(limit=limit)
        elif sort == "hot":
            submissions = user.submissions.hot(limit=limit)
        elif sort == "top":
            submissions = user.submissions.top(time_filter=time_filter, limit=limit)
        elif sort == "controversial":
            submissions = user.submissions.controversial(
                time_filter=time_filter, limit=limit
            )
        else:
            submissions = user.submissions.new(limit=limit)

        # Process posts
        posts = []
        for post in submissions:
            post_obj = RedditPost(
                id=post.id,
                title=post.title,
                author=post.author.name if post.author else "[deleted]",
                subreddit=post.subreddit.display_name,
                selftext=post.selftext,
                url=post.url,
                score=post.score,
                upvote_ratio=post.upvote_ratio,
                num_comments=post.num_comments,
                created_utc=post.created_utc,
                is_self=post.is_self,
                permalink=f"https://www.reddit.com{post.permalink}",
                is_video=post.is_video,
                over_18=post.over_18,
                stickied=post.stickied,
                spoiler=post.spoiler if hasattr(post, "spoiler") else False,
            )
            posts.append(post_obj)

        # Create result
        result = {
            "username": username,
            "posts": [post.model_dump() for post in posts],
            "count": len(posts),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get User Posts")


def mcpgettopsubreddits(
    client_id: str = Field(description="Reddit API client ID"),
    client_secret: str = Field(description="Reddit API client secret"),
    limit: int = Field(
        default=25, description="Number of subreddits to retrieve (max 100)"
    ),
    include_nsfw: bool = Field(
        default=False, description="Whether to include NSFW subreddits"
    ),
) -> str:
    """
    Get list of top/popular subreddits.

    Args:
        client_id: Reddit API client ID
        client_secret: Reddit API client secret
        limit: Number of subreddits to retrieve (max 100)
        include_nsfw: Whether to include NSFW subreddits

    Returns:
        JSON string containing top subreddits
    """
    try:
        # Validate input
        if limit > 100:
            limit = 100
            logger.warning("Limit capped at 100 subreddits")

        # Initialize Reddit API
        reddit = get_reddit_instance(client_id, client_secret)

        # Get popular subreddits
        subreddits = []
        for subreddit in reddit.subreddits.popular(
            limit=limit * 2
        ):  # Get more to account for filtering
            if len(subreddits) >= limit:
                break

            # Skip NSFW subreddits if not included
            if not include_nsfw and subreddit.over18:
                continue

            subreddit_obj = RedditSubreddit(
                id=subreddit.id,
                display_name=subreddit.display_name,
                title=subreddit.title,
                description=subreddit.description,
                subscribers=subreddit.subscribers,
                created_utc=subreddit.created_utc,
                over18=subreddit.over18,
                public_description=subreddit.public_description,
                url=f"https://www.reddit.com{subreddit.url}",
            )
            subreddits.append(subreddit_obj)

        # Create result
        result = {
            "subreddits": [subreddit.model_dump() for subreddit in subreddits],
            "count": len(subreddits),
        }

        return json.dumps(result)

    except Exception as e:
        return handle_error(e, "Get Top Subreddits")


# Main function
if __name__ == "__main__":
    run_mcp_server(
        "Reddit Server",
        funcs=[
            mcpgethotposts,
            mcpsearchreddit,
            mcpgetpostcomments,
            mcpgetsubredditinfo,
            mcpgetuserinfo,
            mcpgetuserposts,
            mcpgettopsubreddits,
        ],
        port=4448,
    )
