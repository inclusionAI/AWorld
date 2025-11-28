---
name: read large webpage or knowledge
description: This skill is used for segmented reading and organization when facing large-scale knowledge bases or web pages. It captures original content segment by segment, summarizes key points in real-time, and continuously deposits them into the knowledge base, ensuring orderly information ingestion, clear structure, and traceability.
tool_list: {"ms-playwright": [], "amnicontext-server": []}
type: agent
---
### 🧠 Knowledge Base
- **Target Scenarios**: Reading long technical documents, research reports, policy documents, web encyclopedias, etc.
- **Core Capabilities**: Segment-based retrieval of original text, real-time summarization, and knowledge network construction.
- **Supporting Tools**: `get_knowledge_by_lines` (segment-by-segment reading), `add_knowledge` (incremental summary writing).

### 📥 Input Specification
Before starting to read, the following should be clarified:
1. The identifier of the knowledge resource to be read (e.g., URL, document ID, file path).
2. The number of lines or paragraph size to pull each time.
3. The current question or topic of focus, to maintain focus during summarization.
4. Output format requirements (paragraph summaries, bullet points, continuous records, etc.).

### 🛠️ Processing Pipeline
1. **Locate Range**: Determine the starting line number and reading length based on user input, and record offsets when necessary for continuation.
2. **Segment-by-Segment Reading**: Call `get_knowledge_by_lines` to pull the original content of the specified range. If the content is too long, it can be scheduled in multiple batches, and record the remaining unread ranges.
3. **Real-Time Analysis**: Extract key points from the pulled segments, annotate keywords, key information, potential issues, or data.
4. **Knowledge Deposition**: Write the refined key points into the knowledge base through `add_knowledge`, along with source line numbers, timestamps, or context descriptions, maintaining structure.
5. **Iterative Progress**: Repeat steps 2-4 until the entire text is read or the user-defined target depth is reached, while maintaining progress indices for recovery.
6. **Global Review**: At periodic nodes, merge stored summaries, generate overall context maps or summaries, and identify missing information.

### 🔁 Iterative Tips
- If cross-segment comparison is needed, it is recommended to preserve original fragment IDs for traceability.
- For key concepts, additional reasoning skills can be called for verification or expansion.
- It is recommended to record unanswered questions in summaries, which should be prioritized when continuing to consult later.

### 📤 Output Template
```
📍 Reading Progress
- Source: ...
- Range: Line ... - ...
- Remaining: ...

📝 Summary Points
- Point 1: ...
- Point 2: ...
- Point 3: ...

🧾 Stored Knowledge
- Knowledge ID: ...
- Summary: ...
- Reference: ...

⚠️ Pending Issues
- ...
```

### ✅ Output Checklist
- Is the reading range and remaining progress accurately annotated?
- Does the summary cover key information and context?
- Have key points been promptly written to the knowledge base and linked to sources?
- Have unresolved issues or parts requiring in-depth exploration been recorded?