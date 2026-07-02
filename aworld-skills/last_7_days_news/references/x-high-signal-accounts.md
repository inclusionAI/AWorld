# High-Signal X Accounts

When the user asks for recent AI trends, opportunities, or X sentiment from the last 7 days, sample these profile timelines before touching Home feed.

## Recommended order

1. Start with model companies and official accounts to capture announcements, product moves, safety updates, and pricing or packaging signals.
2. Move to core people and researchers to capture directional takes, disagreements, and longer-horizon implications.
3. Finish with AI coding and agent-tool ecosystem accounts to capture developer workflow changes, ecosystem spread, and practical opportunity areas.

If a profile page fails with `net::ERR_ABORTED`:

- retry once
- if it still fails, skip it and continue
- backfill with another account from the same group

If the search page shows `Something went wrong. Try reloading.`:

- click `Retry` only once
- if it still fails, abandon search and return to timeline sampling

## Default stable group

These accounts have the highest priority and should serve as the default sampling pool. If the user simply asks for recent AI trends or opportunities, start with 3 to 6 accounts from this section.

### Model companies / official

- `@OpenAI`
- `@AnthropicAI`
- `@GoogleDeepMind`
- `@huggingface`
- `@openclaw`

### Core people / researchers

- `@sama`
- `@geoffreyhinton`
- `@steipete`

### AI coding / agent-tool ecosystem

- `@llama_index`
- `@LangChain`
- `@jerryjliu0`
- `@hwchase17`
- `@ClementDelangue`
- `@SimonWillison`
- `@LoganMarkewich`

## Extended observation group

These accounts are valuable but should not be scanned by default on every run.
Only sample them when the topic clearly requires expansion or when the stable group does not provide enough evidence.

The priority order for expansion is:

1. `model company`
2. `AI coding`
3. `Chinese community`

### P1: model company

- `@gdb`
- `@demishassabis`
- `@mustafasuleyman`
- `@ilyasut`
- `@DanielaAmodei`
- `@aidangomez`
- `@alexandr_wang`
- `@AravSrinivas`
- `@kevinweil`
- `@mikeyk`
- `@JeffDean`
- `@ylecun`
- `@AndrewYNg`
- `@karpathy`
- `@DrJimFan`
- `@rasbt`

### P2: AI coding

- `@julien_c`
- `@mntruell`
- `@antonosika`
- `@rauchg`
- `@ScottWu46`
- `@JonathanRoss321`

### P3: Chinese community

- `@dotey`
- `@xiaohu`
- `@WaytoAGI`
- `@JefferyTatsuya`
- `@oran_ge`

## Sampling guidance

- Broad AI trends: cover at least 2 official accounts, 1 core person, and 2 tooling ecosystem accounts.
- AI coding and agents: prioritize `@OpenAI`, `@AnthropicAI`, `@llama_index`, `@LangChain`, `@SimonWillison`, and `@LoganMarkewich`.
- Model capability and pricing shifts: prioritize `@OpenAI`, `@AnthropicAI`, and `@GoogleDeepMind`.
- Open source and community diffusion: prioritize `@huggingface`, `@ClementDelangue`, and `@jerryjliu0`.
- If the default stable group is already sufficient, do not expand into the extended observation group.
- The extended observation group is better for topic enrichment than for mandatory full traversal.
- Before expanding, ask the user a short confirmation:
  `The stable group already supports a conclusion. Do you want to expand into model company / AI coding / Chinese community?`
- If the user does not explicitly ask for expansion, stay on the stable group by default.

## Removal rules

- Do not keep accounts with clearly no updates in the last 90 days inside the default stable group.
- Do not keep accounts that fail to load for long periods, rarely show visible timelines, or contribute little to recent-trend analysis inside the default stable group.
- In the latest review pass, `@jackclarkSF` was excluded because the latest visible update was older than 90 days.

## What to watch for

- Whether product entry points are moving from chat toward concrete workflows such as Word, Excel, IDEs, or agent platforms
- Whether new narratives appear around multi-model collaboration, advisor/executor splits, routing, or cost/performance tradeoffs
- Whether high-engagement posts mention pricing, rate limits, packaging, team collaboration, or safety response
- Whether infrastructure signals appear around voice, browser use, coding agents, tool calling, memory, or evaluation
