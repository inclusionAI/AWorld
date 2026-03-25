# AWORLD.md Feature Guide

## Overview

The AWORLD.md feature allows you to provide project-specific context to AWorld agents through markdown files. This context is automatically loaded and injected into the agent's system prompt at session start.

## Quick Start

1. Create an `AWORLD.md` file in one of these locations:
   - `~/.aworld/AWORLD.md` (user-level, global - **highest priority**)
   - `.aworld/AWORLD.md` (project-specific)
   - `AWORLD.md` (project root - **lowest priority**)

2. Write your project context in markdown format

3. The content will be automatically loaded when agents start

## Features

### Basic Usage

```markdown
# My Project Context

This project builds a web scraper using AWorld.

## Important Rules
- Always respect robots.txt
- Rate limit to 1 request per second
- Handle errors gracefully
```

### Import Other Files

Use `@filename.md` syntax to import content from other files:

```markdown
# Main Context

@guidelines/coding-standards.md
@guidelines/architecture.md

## Project-Specific Notes
- API key is in .env file
```

### Nested Imports

Imports can be nested - imported files can import other files:

**AWORLD.md:**
```markdown
@guidelines/main.md
```

**guidelines/main.md:**
```markdown
# Guidelines

@guidelines/python.md
@guidelines/testing.md
```

## CLI Commands

The AWorld CLI provides convenient commands to manage your AWORLD.md file:

### `/memory` - Edit AWORLD.md

Opens the AWORLD.md file in your default editor (set via `$EDITOR` or `$VISUAL` environment variable).

```bash
# In aworld-cli interactive mode
You: /memory
```

If no AWORLD.md file exists, it will create one with a template in `.aworld/AWORLD.md`.

**Supported Editors:**
- Set `VISUAL` environment variable for GUI editors (e.g., `code`, `subl`)
- Set `EDITOR` environment variable for terminal editors (e.g., `vim`, `nano`)
- Default: `nano`

**Example:**
```bash
# Use VS Code
export VISUAL=code
aworld-cli

# Use Vim
export EDITOR=vim
aworld-cli
```

### `/memory view` - View Current Content

Displays the current AWORLD.md content in a formatted panel.

```bash
You: /memory view
```

### `/memory status` - Show Status

Displays information about the memory system:
- AWORLD.md file location
- File size and last modified time
- System status

```bash
You: /memory status
```

### `/memory reload` - Reload Memory

Informs you that memory will be reloaded on next agent start. The AWORLD.md file is automatically loaded when agents initialize.

```bash
You: /memory reload
```

## Configuration

### Enable/Disable

In your agent configuration:

```python
from aworld.core.context.amni.config import AgentContextConfig

# Enable (default)
config = AgentContextConfig(
    enable_aworld_file=True,
)

# Disable
config = AgentContextConfig(
    enable_aworld_file=False,
)
```

### Custom Path

Override the default search locations:

```python
config = AgentContextConfig(
    enable_aworld_file=True,
    aworld_file_path="/path/to/custom/AWORLD.md",
)
```

## Best Practices

### 1. Keep It Focused

Only include information that's relevant to the agent's tasks:
- Project-specific conventions
- Important constraints
- Key file locations
- Custom instructions

### 2. Use Imports for Organization

Break large contexts into logical files:
```
.aworld/
├── AWORLD.md           # Main file with imports
├── coding-style.md     # Code style guidelines
├── architecture.md     # Architecture overview
└── api-docs.md         # API documentation
```

### 3. Version Control

Commit AWORLD.md files to git so the entire team benefits:
```bash
git add .aworld/AWORLD.md
git commit -m "Add project context for AI agents"
```

### 4. Keep It Updated

Update AWORLD.md when:
- Project conventions change
- New important patterns emerge
- Architecture evolves

## Examples

### Example 1: Web Development Project

```markdown
# Web App Project Context

## Tech Stack
- Frontend: React + TypeScript
- Backend: FastAPI + Python
- Database: PostgreSQL

## Coding Standards
- Use functional components in React
- Follow REST API conventions
- Write integration tests for all endpoints

## Important Files
- `src/api/`: Backend API routes
- `src/components/`: React components
- `tests/`: Test suite
```

### Example 2: Data Science Project

```markdown
# ML Pipeline Project

## Environment
- Python 3.10+
- CUDA 11.8 for GPU support

## Data Guidelines
- All data in `data/` directory
- Use pandas for data manipulation
- Document all preprocessing steps

## Model Training
- Save checkpoints every 1000 steps
- Log metrics to wandb
- Use config files for hyperparameters
```

## Troubleshooting

### File Not Found

If AWORLD.md isn't being loaded:
1. Check the file exists in one of the search locations
2. Verify the working directory is set correctly
3. Check logs for "Found AWORLD.md at:" message

### Import Errors

If imports aren't working:
1. Verify the imported file exists
2. Check the path is relative to the importing file
3. Look for "Import not found" comments in the output

### Circular Imports

If you have circular imports (A imports B, B imports A):
- The system will detect this and show a warning
- Fix by restructuring your imports

## Technical Details

### Implementation

- **Neuron**: `AWORLDFileNeuron`
- **Priority**: 50 (higher than basic, lower than task)
- **Caching**: Content is cached and reloaded only when file changes
- **Import Pattern**: `^@(.+\.md)\s*$` (regex)

### Search Order

1. `.aworld/AWORLD.md` in working directory
2. `AWORLD.md` in working directory
3. `~/.aworld/AWORLD.md` in home directory

### Performance

- Files are cached after first load
- Modification time is checked before reloading
- Circular imports are detected efficiently

## Contributing

To improve this feature:
1. Report issues on GitHub
2. Suggest enhancements
3. Submit pull requests

## License

This feature is part of AWorld and follows the same license.

---

**Created**: 2024-03-24
**Version**: 1.0
