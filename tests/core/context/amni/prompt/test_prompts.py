from aworld.core.context.amni.prompt.prompts import AMNI_CONTEXT_PROMPT


def test_knowledge_part_mentions_supported_workspace_readback_tools():
    prompt = AMNI_CONTEXT_PROMPT["KNOWLEDGE_PART"]

    assert "list_knowledge_info(limit, offset)" in prompt
    assert "get_knowledge_by_id(knowledge_id)" in prompt
    assert "grep_knowledge(knowledge_id, pattern)" in prompt
    assert "get_knowledge_by_lines(knowledge_id, start_line, end_line)" in prompt
    assert "search_knowledge(user_query, top_k)" in prompt
    assert "get_knowledge(knowledge_id)" not in prompt
    assert "get_knowledge_chunk(knowledge_id, chunk_index)" not in prompt
    assert "search_knowledge_chunks(query)" not in prompt
