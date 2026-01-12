# VeRL Backend (AWorld Train)

This module hosts the VeRL integration for AWorld training workflows.

- aworld_agent_loop.py: Base class bridging VERL AgentLoop with AWorld agents.
- verl_trainer.py: VeRL trainer wrapper.
- agent_template.py: Template for new agents, supported `Agent` only now.
- verl_provider.py: LLM provider for agents.

## Adding New Features
- Avoid putting example-specific code here; that belongs in train/examples/.

## Notes
- Prefer small, composable utilities and explicit public APIs.
