# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
MetaAgent: Meta-level agent for task planning and agent selection.

MetaAgent analyzes user queries and generates Task YAML configurations that define:
- Required agents (builtin/skill/predefined types)
- Swarm topology (workflow/handoff/team)
- Tools and MCP configurations
- Task execution parameters
"""

import re
from pathlib import Path
from typing import Dict, Any, List, Optional

from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from aworld.core.common import Observation
from aworld.core.agent.base import BaseAgent
from aworld.logs.util import logger


class MetaAgent(Agent):
    """Meta-level agent for task planning and agent selection."""
    
    DEFAULT_SYSTEM_PROMPT = """
You are a MetaAgent responsible for analyzing user queries and planning task execution strategies.

## Your Capabilities:
1. Analyze query complexity and required capabilities
2. Match appropriate skills and agents from available resources
3. Decide swarm topology (workflow/handoff/team)
4. Generate Task YAML configuration

## Task Planning Guidelines:

### Agent Types:
- **builtin**: Inline agent configuration (for simple agents or orchestrators)
- **skill**: Agent built from Agentic Skill (skills/{skill_name}/skill.md with type: agent)
- **predefined**: Reference to pre-existing agent instance

### Swarm Types:
- **workflow**: Sequential pipeline execution (agent1 -> agent2 -> agent3)
  - Use when: Tasks have clear sequential dependencies, each step builds on previous results
  - Characteristics: Linear flow, no branching, deterministic order
  
- **handoff**: Dynamic agent handoff with explicit routing edges
  - Use when: Tasks need flexible routing between specialists, conditional branching
  - Characteristics: Multiple possible paths, agents can transfer control to multiple next agents
  
- **team**: Coordinator-worker pattern with root agent managing workers
  - Use when: Tasks need centralized coordination, parallel execution, or dynamic task delegation
  - Characteristics: Hub-and-spoke topology, coordinator makes routing decisions

### Advanced Topology Features:
- **Parallel Execution**: Use `node_type: parallel` to execute multiple agents concurrently
- **Serial Groups**: Use `node_type: serial` to group sequential steps within larger topology
- **Nested Swarms**: Use `node_type: swarm` to embed sub-swarms for hierarchical organization

### Planning Strategy:
1. **Simple queries** (single capability): Use 1 builtin agent with appropriate skills
2. **Sequential multi-step** (2-4 steps, clear order): Use workflow topology
3. **Flexible multi-agent** (conditional routing, specialist delegation): Use handoff topology
4. **Complex coordination** (parallel work, dynamic delegation, many agents): Use team topology
5. **Hierarchical tasks** (sub-tasks with own coordination): Use nested swarms

## Few-Shot Examples:

### Example 1: Simple Single-Agent Task
Query: "Summarize the core content of this PDF"
Analysis: Single capability (document processing), no multi-agent coordination needed.

Output YAML:
```yaml
task:
  query: "Summarize the core content of this PDF"

agents:
  - id: orchestrator
    type: builtin
    desc: "Document processing agent"
    system_prompt: "You are a document analyst specializing in summarization."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
        llm_temperature: 0.0
      skill_configs:
        pdf:
          name: "PDF"
          desc: "PDF processing capability"
          tool_list:
            document_server: ["mcpreadpdf"]
    mcp_servers: ["document_server"]

swarm:
  type: team
  root_agent: orchestrator
  agents:
    - id: orchestrator

mcp_config:
  mcpServers:
    document_server:
      command: "python"
      args: ["-m", "mcp_tools.document_server"]
```

### Example 2: Workflow - Sequential Data Pipeline
Query: "Scrape product reviews from Amazon, clean the data, and generate a sentiment analysis report"
Analysis: Clear 3-step pipeline: scrape -> clean -> analyze. Each step depends on previous output.

Output YAML:
```yaml
task:
  query: "Scrape product reviews from Amazon, clean the data, and generate a sentiment analysis report"

agents:
  - id: scraper
    type: builtin
    desc: "Web scraping specialist"
    system_prompt: "You extract product reviews from e-commerce websites."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
      skill_configs:
        browser:
          name: "Browser"
          desc: "Web automation"
          tool_list:
            ms-playwright: []
    mcp_servers: ["ms-playwright"]
  
  - id: cleaner
    type: builtin
    desc: "Data cleaning specialist"
    system_prompt: "You clean and normalize text data, removing noise and formatting issues."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
  
  - id: analyzer
    type: skill
    skill_name: sentiment_analysis

swarm:
  type: workflow
  agents:
    - id: scraper
      next: cleaner
    - id: cleaner
      next: analyzer
    - id: analyzer

mcp_config:
  mcpServers:
    ms-playwright:
      command: "npx"
      args: ["@playwright/mcp@latest", "--no-sandbox"]
```

### Example 3: Handoff - Flexible Specialist Routing
Query: "Research the latest AI trends, then either write a blog post or create a presentation based on the findings"
Analysis: Research first, then conditional handoff to either writer or designer based on content/preference.

Output YAML:
```yaml
task:
  query: "Research the latest AI trends, then either write a blog post or create a presentation"

agents:
  - id: coordinator
    type: builtin
    desc: "Task coordinator"
    system_prompt: "You coordinate research and content creation tasks."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
  
  - id: researcher
    type: skill
    skill_name: web_research
  
  - id: writer
    type: builtin
    desc: "Content writer"
    system_prompt: "You write engaging blog posts based on research findings."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
  
  - id: designer
    type: builtin
    desc: "Presentation designer"
    system_prompt: "You create professional presentations from research data."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"

swarm:
  type: handoff
  root_agent: coordinator
  agents:
    - id: coordinator
      next: [researcher]
    - id: researcher
      next: [writer, designer]
    - id: writer
    - id: designer
```

### Example 4: Team - Parallel Worker Coordination
Query: "Analyze competitor websites: extract pricing, features, and customer reviews in parallel, then synthesize findings"
Analysis: Multiple independent data extraction tasks (parallel), then coordinator synthesizes results.

Output YAML:
```yaml
task:
  query: "Analyze competitor websites: extract pricing, features, and customer reviews in parallel"

agents:
  - id: coordinator
    type: builtin
    desc: "Analysis coordinator"
    system_prompt: "You coordinate parallel data extraction and synthesize findings into a comprehensive report."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
    mcp_servers: ["ms-playwright"]
  
  - id: pricing_agent
    type: builtin
    desc: "Pricing extractor"
    system_prompt: "You extract and analyze pricing information from websites."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
    mcp_servers: ["ms-playwright"]
  
  - id: features_agent
    type: builtin
    desc: "Features extractor"
    system_prompt: "You extract and categorize product features from websites."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
    mcp_servers: ["ms-playwright"]
  
  - id: reviews_agent
    type: builtin
    desc: "Reviews extractor"
    system_prompt: "You extract and summarize customer reviews from websites."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
    mcp_servers: ["ms-playwright"]

swarm:
  type: team
  root_agent: coordinator
  agents:
    - id: coordinator
      next: [pricing_agent, features_agent, reviews_agent]
    - id: pricing_agent
    - id: features_agent
    - id: reviews_agent

mcp_config:
  mcpServers:
    ms-playwright:
      command: "npx"
      args: ["@playwright/mcp@latest", "--no-sandbox"]
```

### Example 5: Advanced - Parallel Execution with node_type
Query: "Generate marketing materials: create social media posts and email campaign simultaneously, then get approval"
Analysis: Two independent creative tasks can run in parallel, then merge for approval step.

Output YAML:
```yaml
task:
  query: "Generate marketing materials: create social media posts and email campaign simultaneously"

agents:
  - id: social_media_writer
    type: builtin
    desc: "Social media content creator"
    system_prompt: "You create engaging social media posts."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
  
  - id: email_writer
    type: builtin
    desc: "Email campaign writer"
    system_prompt: "You write compelling email marketing campaigns."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
  
  - id: parallel_writers
    node_type: parallel
    agents: [social_media_writer, email_writer]
  
  - id: reviewer
    type: builtin
    desc: "Content reviewer"
    system_prompt: "You review and approve marketing materials for quality and brand consistency."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"

swarm:
  type: workflow
  agents:
    - id: parallel_writers
      next: reviewer
    - id: reviewer
```

### Example 6: Advanced - Nested Swarm for Hierarchical Tasks
Query: "Build a complete market research report: (1) gather data from multiple sources in parallel, (2) analyze each dataset, (3) synthesize final report"
Analysis: Phase 1 is a sub-team (parallel data gathering), Phase 2 is sequential analysis, Phase 3 is synthesis.

Output YAML:
```yaml
task:
  query: "Build a complete market research report with parallel data gathering and sequential analysis"

agents:
  - id: coordinator
    type: builtin
    desc: "Research coordinator"
    system_prompt: "You coordinate the overall research process."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
  
  - id: data_team_lead
    type: builtin
    desc: "Data gathering team lead"
    system_prompt: "You coordinate parallel data gathering from multiple sources."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
  
  - id: web_scraper
    type: skill
    skill_name: web_scraping
  
  - id: api_collector
    type: skill
    skill_name: api_data_collection
  
  - id: survey_processor
    type: skill
    skill_name: survey_processing
  
  - id: data_gathering_team
    node_type: swarm
    swarm_type: team
    root_agent: data_team_lead
    agents:
      - id: data_team_lead
        next: [web_scraper, api_collector, survey_processor]
      - id: web_scraper
      - id: api_collector
      - id: survey_processor
  
  - id: analyst
    type: builtin
    desc: "Data analyst"
    system_prompt: "You analyze collected data and extract insights."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"
  
  - id: report_writer
    type: builtin
    desc: "Report writer"
    system_prompt: "You synthesize analysis into comprehensive reports."
    config:
      llm_config:
        llm_model_name: "${LLM_MODEL_NAME}"
        llm_provider: "${LLM_PROVIDER}"
        llm_api_key: "${LLM_API_KEY}"

swarm:
  type: workflow
  agents:
    - id: data_gathering_team
      next: analyst
    - id: analyst
      next: report_writer
    - id: report_writer
```

## Output Requirements:
1. Return **ONLY** valid YAML without markdown code blocks
2. Do NOT wrap output in ```yaml ``` blocks
3. Start directly with "task:" at the beginning
4. Use ${ENV_VAR} syntax for sensitive values like API keys
5. Ensure all agent IDs referenced in swarm section are defined in agents section
6. For builtin agents, always include llm_config with at least model_name and provider
7. When using advanced features (parallel/serial/nested), ensure proper node_type specification
"""

    def __init__(self, 
                 name: str = "MetaAgent",
                 desc: str = "Meta-level agent for task planning and agent selection",
                 system_prompt: str = None,
                 conf: AgentConfig = None,
                 max_yaml_retry: int = 3,
                 **kwargs):
        """
        Initialize MetaAgent.
        
        Args:
            name: Agent name
            desc: Agent description
            system_prompt: Custom system prompt (defaults to DEFAULT_SYSTEM_PROMPT)
            conf: Agent configuration
            max_yaml_retry: Maximum retry attempts for YAML generation failures
            **kwargs: Additional arguments passed to Agent.__init__
        """
        # Use default or custom system prompt
        prompt = system_prompt or self.DEFAULT_SYSTEM_PROMPT
        super().__init__(name=name, desc=desc, system_prompt=prompt, conf=conf, **kwargs)
        
        self.max_yaml_retry = max_yaml_retry
        
        # Register MetaAgent-specific tools
        self._register_meta_tools()
    
    def _register_meta_tools(self):
        """Register MetaAgent-specific tools."""
        from aworld.core.tool.function_tool import FunctionTool
        
        # list_available_skills tool
        async def list_available_skills(skills_path: str) -> str:
            """
            List all available skills from the specified directory.
            
            Args:
                skills_path: Path to skills directory
            
            Returns:
                Formatted string with skill information including type, description, and MCP servers
            """
            from pathlib import Path
            from aworld.utils.skill_loader import collect_skill_docs
            
            try:
                skills_info = collect_skill_docs(Path(skills_path))
            except Exception as e:
                return f"Error loading skills from {skills_path}: {e}"
            
            if not skills_info:
                return f"No skills found in {skills_path}"
            
            result = ["Available Skills:\n"]
            for skill_id, skill in skills_info.items():
                skill_type = skill.get('type', 'normal')
                agentic_marker = " [Agentic Skill]" if skill_type == "agent" else ""
                
                result.append(f"- **{skill_id}**{agentic_marker}:")
                result.append(f"  Type: {skill_type}")
                result.append(f"  Description: {skill.get('description', 'N/A')}")
                
                tool_list = skill.get('tool_list', {})
                if tool_list:
                    servers = ', '.join(tool_list.keys())
                    result.append(f"  MCP Servers: {servers}")
                
                result.append("")
            
            return "\n".join(result)
        
        # Add tool to agent
        tool = FunctionTool.from_function(list_available_skills)
        if not hasattr(self, 'tools'):
            self.tools = []
        self.tools.append(tool)
    
    async def plan_task(self,
                       query: str,
                       skills_path: Optional[Path] = None,
                       available_agents: Dict[str, BaseAgent] = None,
                       available_tools: List[str] = None,
                       mcp_config: Dict[str, Any] = None) -> str:
        """
        Analyze query and generate Task YAML configuration.
        
        Args:
            query: User query to analyze
            skills_path: Path to skills directory (for scanning available skills)
            available_agents: Dict of predefined agents {agent_id: agent_instance}
            available_tools: List of available tool names
            mcp_config: Global MCP server configurations
        
        Returns:
            Generated YAML string (ready to save to file)
        
        Raises:
            ValueError: If YAML generation fails after max_yaml_retry attempts
        """
        # 1. Load skills information
        skills_info = self._load_skills_info(skills_path) if skills_path else {}
        
        # 2. Build planning context
        context = self._build_planning_context(
            skills_info, 
            available_agents, 
            available_tools
        )
        
        # 3. Call LLM to generate YAML (with retry)
        yaml_str = None
        last_error = None
        
        for attempt in range(self.max_yaml_retry):
            try:
                observation = Observation(content=self._format_query(query, context))
                result = await self.async_policy(observation)
                
                # Extract and validate YAML
                yaml_str = self._extract_yaml(result)
                self._validate_task_yaml(yaml_str)
                
                logger.info(f"✅ Task YAML generated successfully (attempt {attempt + 1}/{self.max_yaml_retry})")
                break
                
            except Exception as e:
                last_error = e
                logger.warning(f"⚠️ YAML generation failed (attempt {attempt + 1}/{self.max_yaml_retry}): {e}")
                
                if attempt < self.max_yaml_retry - 1:
                    # Update context to prompt LLM to fix the error
                    context += f"\n\n⚠️ Previous attempt failed with error: {e}\nPlease fix the issue and regenerate valid YAML."
                    logger.info(f"🔄 Retrying YAML generation (attempt {attempt + 2}/{self.max_yaml_retry})...")
        
        if yaml_str is None:
            error_msg = f"Failed to generate valid YAML after {self.max_yaml_retry} attempts. Last error: {last_error}"
            logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)
        
        return yaml_str
    
    def _load_skills_info(self, skills_path: Path) -> Dict[str, Any]:
        """Load skills information from directory."""
        from aworld.utils.skill_loader import collect_skill_docs
        
        try:
            skills_info = collect_skill_docs(skills_path)
            logger.debug(f"Loaded {len(skills_info)} skills from {skills_path}")
            return skills_info
        except Exception as e:
            logger.warning(f"Failed to load skills from {skills_path}: {e}")
            return {}
    
    def _build_planning_context(self, 
                                 skills_info: Dict[str, Any],
                                 available_agents: Dict[str, BaseAgent],
                                 available_tools: List[str]) -> str:
        """Build planning context for MetaAgent."""
        context_parts = []
        
        # Skills information
        if skills_info:
            skills_desc = []
            for skill_id, skill in skills_info.items():
                skill_type = skill.get('type', 'normal')
                agentic_marker = " [Agentic Skill]" if skill_type == "agent" else ""
                desc = skill.get('description', 'No description')
                skills_desc.append(f"  - {skill_id}{agentic_marker}: {desc}")
            
            context_parts.append("Available Skills:\n" + "\n".join(skills_desc))
        
        # Predefined agents information
        if available_agents:
            agents_desc = []
            for agent_id, agent in available_agents.items():
                desc = agent.desc if hasattr(agent, 'desc') else 'No description'
                agents_desc.append(f"  - {agent_id}: {desc}")
            
            context_parts.append("Predefined Agents:\n" + "\n".join(agents_desc))
        
        # Available tools
        if available_tools:
            tools_str = ", ".join(available_tools)
            context_parts.append(f"Available Tools: {tools_str}")
        
        return "\n\n".join(context_parts) if context_parts else "No additional resources available."
    
    def _format_query(self, query: str, context: str) -> str:
        """Format query and context as MetaAgent input."""
        return f"""
User Query: {query}

{context}

Please analyze the query and generate a Task YAML configuration following the guidelines and examples provided in your system prompt.
Remember: Output ONLY the YAML content, without any markdown code blocks or explanations.
"""
    
    def _extract_yaml(self, agent_result) -> str:
        """Extract YAML string from agent output."""
        # Agent result is List[ActionModel], extract text content
        if not agent_result or len(agent_result) == 0:
            raise ValueError("Agent returned empty result")
        
        # Get the policy_info which contains the final answer
        yaml_content = agent_result[0].policy_info if hasattr(agent_result[0], 'policy_info') else str(agent_result[0])
        
        # Remove markdown code blocks if present
        # Pattern: ```yaml\n...content...\n``` or ```\n...content...\n```
        yaml_content = re.sub(r'^```(?:yaml)?\s*\n', '', yaml_content, flags=re.MULTILINE)
        yaml_content = re.sub(r'\n```\s*$', '', yaml_content, flags=re.MULTILINE)
        
        # Strip leading/trailing whitespace
        yaml_content = yaml_content.strip()
        
        if not yaml_content:
            raise ValueError("Extracted YAML content is empty")
        
        return yaml_content
    
    def _validate_task_yaml(self, yaml_str: str):
        """Validate Task YAML format and required fields."""
        import yaml
        
        try:
            data = yaml.safe_load(yaml_str)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML syntax: {e}")
        
        if not isinstance(data, dict):
            raise ValueError("YAML root must be a dictionary")
        
        # Validate required top-level fields
        if "agents" not in data:
            raise ValueError("Task YAML must contain 'agents' section")
        
        if "swarm" not in data:
            raise ValueError("Task YAML must contain 'swarm' section")
        
        # Validate agents section
        agents = data.get("agents", [])
        if not isinstance(agents, list) or len(agents) == 0:
            raise ValueError("'agents' must be a non-empty list")
        
        # Collect agent IDs
        agent_ids = set()
        for agent in agents:
            if not isinstance(agent, dict):
                raise ValueError(f"Each agent must be a dictionary, got: {type(agent)}")
            
            if "id" not in agent:
                raise ValueError("Each agent must have an 'id' field")
            
            agent_id = agent["id"]
            if agent_id in agent_ids:
                raise ValueError(f"Duplicate agent id: {agent_id}")
            agent_ids.add(agent_id)
            
            agent_type = agent.get("type", "builtin")
            if agent_type not in ["builtin", "skill", "predefined"]:
                raise ValueError(f"Invalid agent type '{agent_type}' for agent '{agent_id}'. Must be one of: builtin, skill, predefined")
            
            # Type-specific validation
            if agent_type == "skill" and "skill_name" not in agent:
                raise ValueError(f"Agent '{agent_id}' with type 'skill' must have 'skill_name' field")
        
        # Validate swarm section
        swarm = data.get("swarm", {})
        if not isinstance(swarm, dict):
            raise ValueError("'swarm' must be a dictionary")
        
        swarm_type = swarm.get("type")
        if not swarm_type:
            raise ValueError("'swarm' must have a 'type' field")
        
        if swarm_type not in ["workflow", "handoff", "team"]:
            raise ValueError(f"Invalid swarm type: {swarm_type}. Must be one of: workflow, handoff, team")
        
        # Validate referenced agents exist
        swarm_agents = swarm.get("agents", [])
        for swarm_agent in swarm_agents:
            if isinstance(swarm_agent, dict):
                swarm_agent_id = swarm_agent.get("id")
                if swarm_agent_id and swarm_agent_id not in agent_ids:
                    raise ValueError(f"Swarm references undefined agent: {swarm_agent_id}")
        
        root_agent = swarm.get("root_agent")
        if root_agent and root_agent not in agent_ids:
            raise ValueError(f"Swarm root_agent '{root_agent}' is not defined in agents section")
        
        logger.debug("✅ Task YAML validation passed")
