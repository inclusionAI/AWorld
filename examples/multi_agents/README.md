# Multi-Agent Examples

This directory contains a variety of multi-agent system examples built on the AWorld framework. 
These examples demonstrate four core paradigms of agent **collaboration**, **coordination**, **workflow**, and **hybrid**, 
corresponding to **Swarm** of Handoff, Team, Workflow, and Hybrid respectively.

## Examples of Paradigm

- **collaborative/**
  - Multi-agent collaboration scenarios.
    - **debate/**  
      Example of agents engaging in a debate, including affirmative, negative, and moderator agents. Demonstrates turn-based argumentation and multi-agent dialogue.
    - **travel/**  
      Multi-agent interaction for travel planning.  

- **coordination/**
  - Multi-agent coordination and orchestration patterns.
    - **custom_agent/**  
      Example for customizing agent roles and behaviors in a coordinated system.
    - **master_worker/**  
      Demonstrates the TeamSwarm pattern, where a lead agent (PlanAgent) coordinates with specialized agents (SearchAgent, SummaryAgent) to solve complex tasks.  
      Includes both multi-action and single-action planning versions.  
      See `master_worker/README.md` for detailed workflow and advantages of each approach.
    - **deepresearch/**  
      Advanced research scenario with a planner agent, web search agent, and reporting agent.  
      Shows how to break down user queries, plan search strategies, and synthesize results using a TeamSwarm.

- **workflow/**
  - Workflow automation with multi-agent.
    - **search/**  
      Example of agents collaborating to perform search and data aggregation tasks.

- **hybrid/**
  - Hybrid multi-agent system combining centralized coordination with peer-to-peer communication.
    - **data_processing/**  
      Demonstrates Hybrid architecture with a data processing pipeline (filter → transform → validate).  
      Features peer communication between executors for information sharing and quality feedback.  
      Includes both quick architecture tests (no LLM) and full validation with LLM agents.  
      See `hybrid/README.md` for architecture details and `hybrid/data_processing/README.md` for usage.

## Key Concepts

- **Collaboration (Handoff):**  
  Agents work together to achieve a common goal through dynamic delegation, such as debating or planning a trip.
- **Coordination (Team):**  
  Agents are orchestrated in a structured pattern (star topology) to solve complex problems with a central coordinator.
- **Workflow Automation (Workflow):**  
  Agents automate multi-step processes in sequential or parallel DAG patterns, such as planning, searching, and summarizing information.
- **Hybrid (Team + Peer Communication):**  
  Combines centralized coordination with peer-to-peer communication. Orchestrator controls workflow while executors share information directly for better coordination.

## Usage

- Each subdirectory contains its own entry point (usually `run.py`) and may include additional configuration or requirements files.
- Before running any example, ensure you have installed all required dependencies and set the necessary environment variables (e.g., LLM provider credentials, API keys).
- For detailed instructions, refer to the README or comments within each subdirectory.
