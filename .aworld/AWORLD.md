# AWorld Project Context

## Project Overview
AWorld is a powerful framework for building AI agents with advanced context management and memory capabilities.

## Development Guidelines

### Code Style
- Use Python 3.10+ features
- Follow PEP 8 style guide
- Add type hints to all functions
- Write comprehensive docstrings

### Testing
- Write unit tests for all new features
- Maintain test coverage above 80%
- Use pytest for testing
- Mock external dependencies

### Architecture
- Follow the Neuron pattern for prompt components
- Use async/await for all I/O operations
- Implement proper error handling
- Keep context under 100K tokens

## Important Directories
- `aworld/core/context/amni/`: Context management system
- `aworld/core/context/amni/prompt/neurons/`: Neuron implementations
- `aworld/agents/`: Agent implementations
- `tests/`: Test suite

## Custom Instructions for AI Assistants

When working on this codebase:
1. Always check existing patterns before implementing new features
2. Maintain backward compatibility
3. Update documentation when changing APIs
4. Run tests before committing
5. Use the logger for debugging, not print()

## Recent Changes
- Added AWORLDFileNeuron for loading project-specific context from AWORLD.md files
- Supports @import syntax for including other markdown files
- Integrated with SystemPromptAugmentOp for automatic injection

---
*This file is automatically loaded by AWorld agents to provide project-specific context.*
