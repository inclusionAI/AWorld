from math import log

from github import Github

from aworld.logs.util import logger
from aworld.mcp_servers.code_server import mcpgeneratecode
from aworld.mcp_servers.github_server import get_github_instance
from aworld.mcp_servers.utils import get_llm_config_from_os_environ, run_mcp_server
from aworld.models.llm import get_llm_model

# # Initialize GitHub API
# github = get_github_instance()

# # Get repository
# repo = github.get_repo("numpy/numpy")

# # Get issues
# issues = repo.get_issues(state="closed", labels=["06 - Regression"])

# logger.success("Issues retrieved successfully!")
# cnt = 0
# results = []
# for issue in issues:
#     if "polynomial" in issue.title or "polynomial" in issue.body:
#         cnt += 1
#         logger.info(issue.title)
#         results.append(issue)
# logger.success(f"Total issues: {cnt}")

# for result in results:
#     logger.info(result.title)
#     logger.info(result.body)
#     logger.critical(result._created_at.value)
#     logger.critical(result._updated_at.value)
#     logger.critical(result.labels)


code = mcpgeneratecode(
    prompt="Simulate the 'Pick That Ping-Pong' game mechanics to calculate the probability of each ball being ejected. The simulation should model the random firing of pistons and track which ball is ejected first. Use Python to create the simulation.",
    language="python",
)

logger.success(code)
