---
name: OpenClaw
description: Complete guide for OpenClaw installation, Discord configuration, and sending messages, including common issues and solutions
---

# 📚 OpenClaw Installation and Discord Configuration Guide

## 1. Environment Setup

### 1.1 System Requirements
- **Node.js**: v22.16.0+ (required)
- **npm**: Installed with Node.js
- **pnpm**: Must be installed globally
- **Git**: For cloning the repository

### 1.2 PATH Issues (Important!)
On macOS, Node.js may not be in the default PATH; you may need to specify it explicitly:
```bash
/usr/local/bin/node --version
/usr/local/bin/npm --version
```
**Solution**: Prepend `export PATH="/usr/local/bin:$PATH"` before running commands.

---

## 2. Installation Steps

### 2.1 Clone the Repository
```bash
git clone https://github.com/openclaw/openclaw.git
cd openclaw
```

### 2.2 Install Dependencies
```bash
# Install pnpm (if not already installed)
/usr/local/bin/node /usr/local/bin/npm install -g pnpm
# Install project dependencies
export PATH="/usr/local/bin:$PATH"
npm install
```

### 2.3 Build the Project
```bash
export PATH="/usr/local/bin:$PATH"
pnpm build
```
**⚠️ Note**: The build takes about 20–30 seconds and produces the `dist/` directory.

---

## 3. Basic Configuration

### 3.1 Set Gateway Mode
```bash
export PATH="/usr/local/bin:$PATH"
node dist/index.js config set gateway.mode '"local"' --json
```

### 3.2 Configuration File Locations
- **Main config**: `~/.openclaw/openclaw.json`
- **Log files**: `/tmp/openclaw/openclaw-YYYY-MM-DD.log`

---

## 4. Discord Configuration

### 4.1 Create a Discord Bot
1. Go to https://discord.com/developers/applications
2. Create New Application → Bot
3. **Required intents**:
   - ✅ Message Content Intent
   - ✅ Server Members Intent
4. Copy the Bot Token (format: `MTQ3...`)
5. Invite the bot to your server using the OAuth2 URL

### 4.2 Configure Discord Channel
```bash
# Enable Discord
node dist/index.js config set channels.discord.enabled 'true' --json
# Set Bot Token
node dist/index.js config set channels.discord.token '"YOUR_BOT_TOKEN"' --json
```

### 4.3 Verify Configuration
```bash
cat ~/.openclaw/openclaw.json
```
It should include:
```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN"
    }
  },
  "gateway": {
    "mode": "local"
  }
}
```

---

## 5. Starting the Gateway

### 5.1 Run in Background (Recommended)
```bash
export PATH="/usr/local/bin:$PATH"
cd openclaw
# Run in background with nohup
nohup node dist/index.js gateway --port 18789 > /tmp/gateway.log 2>&1 &
# Check process
ps aux | grep openclaw-gateway | grep -v grep
```

### 5.2 Verify Gateway Status
```bash
# Check port
lsof -i :18789
# Check channel status
node dist/index.js channels status --probe
```

---

## 6. Sending Discord Messages

### 6.1 Command Format
```bash
node dist/index.js message send \
  --channel discord \
  --target "user:USER_ID" \
  --message "Message content"
```

### 6.2 Full Example
```bash
export PATH="/usr/local/bin:$PATH"
cd openclaw
node dist/index.js message send \
  --channel discord \
  --target "user:1465280767801430026" \
  --message "🦞 Hello from OpenClaw!"
```

---

## 7. Troubleshooting

### 7.1 Discord Connection Failed: `fetch failed`
**Symptoms**:
```
Error: Failed to get gateway information from Discord: fetch failed
discord: failed to deploy native commands: fetch failed
```
**Possible causes**:
1. **Invalid Bot Token** – Token revoked or expired
2. **Network issues** – Cannot reach discord.com
3. **Intents not enabled** – Message Content Intent is disabled

**Solution**:
```bash
# Verify token
curl -H "Authorization: Bot YOUR_TOKEN" \
  https://discord.com/api/v10/users/@me
```

### 7.2 Gateway Exits Right After Starting
**Symptoms**: Gateway process starts then exits immediately.

**Solution**:
- Check logs: `tail -f /tmp/openclaw/openclaw-YYYY-MM-DD.log`
- Ensure Discord token is valid
- Use `nohup` so it keeps running in the background

### 7.3 `TypeError: fetch failed` When Sending Messages
**Cause**: Gateway not running or Discord not connected.

**Solution**:
1. Check if Gateway is running: `ps aux | grep openclaw-gateway`
2. Check Discord status: `node dist/index.js channels status --probe`
3. Restart Gateway and wait for Discord to connect (about 5–10 seconds)

---

## 8. Command Quick Reference

| Action | Command |
|--------|--------|
| Build project | `pnpm build` |
| Start Gateway | `nohup node dist/index.js gateway --port 18789 > /tmp/gateway.log 2>&1 &` |
| View config | `cat ~/.openclaw/openclaw.json` |
| Set config | `node dist/index.js config set KEY 'VALUE' --json` |
| Send message | `node dist/index.js message send --channel discord --target "user:ID" --message "content"` |
| Check status | `node dist/index.js channels status --probe` |
| View logs | `tail -f /tmp/openclaw/openclaw-YYYY-MM-DD.log` |

---

## 9. Best Practices

1. **Use full paths**: `/usr/local/bin/node` instead of `node`
2. **Run Gateway in background**: Use `nohup` and `&`
3. **Wait for connection**: Discord needs 5–10 seconds to connect; do not send messages immediately
4. **Verify token**: Use curl to verify the Bot token before use
5. **Check logs**: When something fails, check the log file first

---
