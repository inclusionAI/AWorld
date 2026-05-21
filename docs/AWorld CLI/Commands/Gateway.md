# Gateway

## What It Does

The gateway command starts and inspects the multi-channel AWorld gateway. It is the entry point for serving AWorld agents through channels such as DingTalk, WeChat, Telegram, and WeCom.

The current gateway also includes a slash-command bridge for channel messages. Tool commands such as `/memory status` and `/cron status` can execute directly from supported channels without going through normal prompt interpretation.

## Commands

Top-level CLI usage:

```bash
aworld-cli gateway status
aworld-cli gateway channels list
aworld-cli gateway server
```

Useful global options:

```bash
aworld-cli --env-file .env --agent-dir ./agents gateway server
aworld-cli --remote-backend http://localhost:8000 gateway server
```

## Typical Workflow

1. Start by checking `aworld-cli gateway status`.
2. Inspect channel availability with `aworld-cli gateway channels list`.
3. Launch the service with `aworld-cli gateway server`.
4. Send a supported slash command from a configured channel to verify the command bridge.

## Configuration And Files

- Config file: `.aworld/gateway/config.yaml`
- Default host: `127.0.0.1`
- Default port: `18888`
- DingTalk workspace directory defaults to `.aworld/gateway/dingding`
- Gateway logs are written to `logs/gateway.log`

## Notes And Limits

- `gateway status` reports the default agent and per-channel enabled state.
- `gateway channels list` shows both implemented and placeholder channels, so `implemented=false` means the channel exists in the registry but does not yet have a working adapter.
- Phase-1 command bridging is strongest for tool-style slash commands. Prompt-style slash commands require a prompt executor and are not generally available in bridge-only contexts.
