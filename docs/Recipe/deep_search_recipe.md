# Deep Search Recipe

A comprehensive guide to setting up and using the Deep Search capability with AWorld CLI and agent-browser integration.

---

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Step 1: Enable Chrome DevTools Protocol (CDP)](#step-1-enable-chrome-devtools-protocol-cdp)
3. [Step 2: Install agent-browser](#step-2-install-agent-browser)
4. [Step 3: Install the agent-browser Skill](#step-3-install-the-agent-browser-skill)
5. [Step 4: Create Workspace and Launch AWorld CLI](#step-4-create-workspace-and-launch-aworld-cli)
6. [Step 5: Configure Memory Settings](#step-5-configure-memory-settings)
7. [Getting Started](#getting-started)
8. [Troubleshooting](#troubleshooting)

---

## Prerequisites

Before you begin, ensure you have:
- Google Chrome installed on your system
- Node.js and npm installed (for agent-browser)
- AWorld CLI installed and configured
- Terminal access with appropriate permissions

---

## Step 1: Enable Chrome DevTools Protocol (CDP)

The Chrome DevTools Protocol allows external tools to interact with Chrome programmatically. You need to launch Chrome with remote debugging enabled.

### Command

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-work
```

### What This Does

- `--remote-debugging-port=9222`: Opens Chrome with CDP enabled on port 9222
- `--user-data-dir=/tmp/chrome-work`: Uses a temporary profile directory to avoid conflicts with your regular Chrome session

### Important Notes

- Keep this Chrome instance running throughout your Deep Search session
- This creates a separate Chrome profile, so you won't see your regular bookmarks or extensions
- The port 9222 is the default; ensure no other application is using this port

---

## Step 2: Install agent-browser

agent-browser is a powerful tool that enables automated browser interactions for AI agents.

### Installation Steps

1. **Install the agent-browser package globally:**

   ```bash
   npm install -g agent-browser
   ```

2. **Download the required Chromium binary:**

   ```bash
   agent-browser install
   ```

### Reference

- Official Repository: [https://github.com/vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser)
- Documentation: Check the repository for detailed usage instructions

### Verification

After installation, verify that agent-browser is correctly installed:

```bash
agent-browser --version
```

---

## Step 3: Install the agent-browser Skill

Skills extend AWorld's capabilities. The agent-browser skill enables browser automation within your AWorld agents.

### Installation Command

```bash
cp -r ./aworld-skills/agent-browser/ ~/.aworld/skills/
```

### What This Does

- Copies the agent-browser skill directory to your AWorld skills folder
- Makes the skill available to all AWorld agents on your system

### Directory Structure

After installation, you should have:
```
~/.aworld/skills/
└── agent-browser/
    ├── SKILL.md
    └── [other skill files]
```

### Verification

Ensure the skill directory exists:

```bash
ls -la ~/.aworld/skills/agent-browser/
```

---

## Step 4: Create Workspace and Launch AWorld CLI

Set up a dedicated workspace for your Deep Search projects to keep your work organized.

### Commands

1. **Create and navigate to your workspace:**

   ```bash
   mkdir -p ~/deep_search_workspace
   cd ~/deep_search_workspace
   ```

2. **Launch AWorld CLI:**

   ```bash
   aworld-cli
   ```

### What This Does

- Creates a new directory for Deep Search projects (if it doesn't exist)
- Changes to that directory
- Starts the AWorld CLI interface in your workspace

### Workspace Benefits

- Keeps all Deep Search-related files organized
- Provides a clean environment for each session
- Makes it easier to manage project artifacts

---

## Step 5: Configure Memory Settings

Configure AWorld to use the CDP-enabled Chrome instance for browser automation.

### Configuration Steps

1. **Open the memory configuration interface:**

   Type the following command in the AWorld CLI:
   ```
   /memory
   ```

2. **Add the CDP configuration:**

   Add the following instruction to the memory:
   ```
   agent_browser --cdp 9222 使用agent-browser的时候都要加上cdp参数
   ```
   
   **English translation:**
   ```
   When using agent-browser, always include the --cdp 9222 parameter
   ```

### What This Does

- Instructs AWorld agents to connect to your CDP-enabled Chrome instance (port 9222)
- Ensures browser automation commands use the correct Chrome session
- Persists this configuration across your AWorld session

### Why This Matters

Without this configuration, agent-browser would try to launch its own browser instance instead of using your CDP-enabled Chrome, which could lead to connection issues.

---

## Getting Started

Once you've completed all the setup steps, you're ready to use Deep Search!

### Quick Start Checklist

- [ ] Chrome is running with CDP enabled (port 9222)
- [ ] agent-browser is installed and verified
- [ ] agent-browser skill is copied to `~/.aworld/skills/`
- [ ] You're in your workspace directory
- [ ] AWorld CLI is running
- [ ] Memory is configured with CDP settings

### Example Usage

Try asking your AWorld agent to:
- "Search for the latest AI research papers"
- "Browse to example.com and extract the main heading"
- "Find information about [topic] and summarize it"

---

## Troubleshooting

### Common Issues and Solutions

#### Chrome CDP Connection Failed

**Problem:** Agent cannot connect to Chrome on port 9222

**Solutions:**
- Verify Chrome is running with the CDP command
- Check that port 9222 is not blocked by a firewall
- Ensure no other application is using port 9222
- Try restarting Chrome with the CDP command

#### agent-browser Command Not Found

**Problem:** Terminal doesn't recognize the `agent-browser` command

**Solutions:**
- Verify npm global installation: `npm list -g agent-browser`
- Check your PATH includes npm global binaries
- Try reinstalling: `npm install -g agent-browser`

#### Skill Not Loading

**Problem:** AWorld doesn't recognize the agent-browser skill

**Solutions:**
- Verify the skill directory exists: `ls ~/.aworld/skills/agent-browser/`
- Check file permissions: `chmod -R 755 ~/.aworld/skills/agent-browser/`
- Restart AWorld CLI to reload skills

#### Browser Automation Not Working

**Problem:** Commands execute but browser doesn't respond

**Solutions:**
- Confirm memory configuration includes `--cdp 9222`
- Check Chrome DevTools at `http://localhost:9222` to verify CDP is active
- Restart both Chrome (with CDP) and AWorld CLI

---

## Additional Resources

- **AWorld Documentation:** Check official AWorld docs for advanced features
- **agent-browser GitHub:** [https://github.com/vercel-labs/agent-browser](https://github.com/vercel-labs/agent-browser)
- **Chrome DevTools Protocol:** [https://chromedevtools.github.io/devtools-protocol/](https://chromedevtools.github.io/devtools-protocol/)

---

## Enjoy Your Deep Search Experience! 🚀

You're now ready to leverage the full power of automated web browsing and deep search capabilities with AWorld. Happy exploring!
