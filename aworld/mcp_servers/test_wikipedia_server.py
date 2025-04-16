import json
import pytest
from loguru import logger

from aworld.mcp_servers.wikipedia_server import (
    mcpwikisearch,
    mcpwikicontent,
    mcpwikisummary,
    mcpwikirandom,
    mcpwikicategories,
    mcpwikilinks,
    mcpwikihistory,
)

def test_wikipedia_history():
    """Test Wikipedia history retrieval"""
    history_result = mcpwikihistory(
        title="Holabird & Roche",
        date="2023/06",  # Testing with just year/month
        language="en"
    )
    history_data = json.loads(history_result)
    
    # Print detailed response data
    print("\n=== History Data Details ===")
    print(json.dumps(history_data, indent=2))
    print("===========================\n")
    
    assert "results" in history_data
    assert "content" in history_data["results"]
    assert "requested_date" in history_data["results"]
    assert "actual_date" in history_data["results"]
    assert "is_exact_date" in history_data["results"]


if __name__ == "__main__":
    pytest.main([__file__]) 