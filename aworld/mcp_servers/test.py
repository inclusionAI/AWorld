import json

from loguru import logger
from search_exa import mcpsearchquery

if __name__ == "__main__":
    query = "AI regulation paper arXiv June 2022"
    logger.info(f"Query: {query}")

    results = mcpsearchquery(query)
    logger.success(f"Results: {json.dumps(results, indent=4)}")
