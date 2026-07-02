---
name: last_7_days_news
description: Search and summarize the latest 7 days of AI news and X discussions using public sources plus browser-based X collection. Use for recent AI news, trends, X discussions, industry briefs, and summaries organized into hot topics, viewpoints, and opportunity areas.
---

# Last 7 Days News

This skill organizes the latest 7 days of AI news and X discussion signals.

The X workflow is intentionally opinionated:

- use a validated X cookie stored only at `/tmp/last_7_days_news_x_cookie.txt`
- sample high-signal profile timelines first
- use Home feed only as a supplement
- use the search page only as a final fallback
- collect page content through `agent-browser`
- filter by time window, topic relevance, and engagement in the conversation
- optionally generate an HTML report and a one-page summary

Do not treat `collect_x_feed.py` or Home feed as the default primary entry point.

## When To Use

Use this skill when the user wants:

- AI, technology, or industry news from the last 7 days
- recent or high-signal X discussions from the last 7 days
- a merged brief across public news and social discussion
- a trend summary organized as `Hot Topics / Viewpoints / Opportunities`
- coverage of OpenAI, Anthropic, Claude, Gemini, MCP, agents, coding tools, or adjacent AI themes

## Default Workflow

### 1. Lock the topic, time window, and source scope

- The default time window is the last `7` days.
- If the user does not specify sources, cover all of the following in the same run:
  - official blogs, news sites, and documentation
  - developer communities such as Hacker News, GitHub, and Reddit
  - X discussion signals
- Treat these as parallel evidence tracks rather than a single linear path.
- If the user does not specify an output structure, use:
  - `Hot Topics`
  - `Viewpoints`
  - `Opportunities`
- If the user does not specify an output language, follow the current conversation language.

### 2. Expand public-source exploration

In one evidence track, collect stable sources that do not require extra accounts:

- Hacker News
- GitHub
- RSS feeds and official blogs
- web search and official documentation

Prioritize high-signal information:

- official announcements
- product updates with clear dates
- highly discussed community posts
- implementation notes and developer writeups

### 3. Expand X exploration in parallel

In a second evidence track, run the X portion in this order:

1. reuse and validate the `/tmp` cookie
2. sample high-signal account timelines
3. add Home feed only when timeline samples are insufficient
4. try a narrow search query only when both timeline and Home feed are insufficient
5. normalize, deduplicate, and cross-check the X findings against the public-source track

Do not start from the search page, and do not rely on Home feed alone for broad AI conclusions.

### 4. Merge, cross-check, and rank the evidence

After the public-source track and the X track have both explored the topic:

- merge the normalized results into one candidate set
- deduplicate overlapping items
- cross-check social claims against stable public links whenever possible
- rank the merged set by:
  - original source quality
  - freshness
  - engagement or discussion intensity
  - relevance to the user's ask

The default mental model is:

- one track explores public sources
- one track explores X signals
- the result layer merges both before summarization

### 5. X cookie rules

Only use this cookie path:

- `/tmp/last_7_days_news_x_cookie.txt`

The minimum required cookie fields are usually:

- `auth_token`
- `ct0`

If a cookie file exists, validate it first with `scripts/validate_x_cookies.py`.
Do not trust file existence alone.

Preferred login refresh flow:

1. run `scripts/ensure_x_cookies.sh`
2. if login is needed, complete login in the opened browser
3. export cookies with `scripts/export_x_cookies.py`
4. validate the refreshed cookie again with `scripts/validate_x_cookies.py`

`export_x_cookies.py` writes only to `/tmp/last_7_days_news_x_cookie.txt`.

### 6. Keyword handling

Keywords still matter, but they are filters rather than the default collection entry point.

Preferred keyword input:

- ask the user for a keyword file when one already exists
- read one keyword per line
- ignore empty lines and `#` comment lines
- normalize to lowercase for matching

If the user does not have a file, build a temporary in-memory list inside the current run.

Example keywords:

```text
chatgpt
claude
gemini
openai
anthropic
mcp
agent
ai coding
```

Extra rules:

- avoid using the bare keyword `ai` as a hard Home feed filter because the false-positive rate is high
- prefer brand names and compound phrases such as `openai`, `anthropic`, `claude`, `gemini`, `mcp`, `ai agent`, and `coding agent`
- when sampling a high-signal account timeline, let the account signal and time window dominate keyword filtering

### 7. Browser collection mode

Prefer direct `agent-browser` actions over ad hoc scraping scripts.

Default browser flow:

1. open `https://x.com`
2. inject cookies from `/tmp/last_7_days_news_x_cookie.txt`
3. refresh the page
4. open `https://x.com/home` to confirm the authenticated state
5. open high-signal profile timelines one by one
6. use `agent-browser snapshot` to read visible content
7. continue with `scroll down`, `click`, `tab`, and `back`

The high-signal account list lives here:

- `references/x-high-signal-accounts.md`

Rules for account groups:

- start with the default stable group
- use the extended observation group only when the topic clearly needs it or when the stable group is not enough
- the extended observation priority is `model company -> AI coding -> Chinese community`
- ask the user for a short confirmation before expanding beyond the stable group

Recommended sampling order:

1. model companies and official accounts
2. core people and researchers
3. AI coding and agent-tool ecosystem

For tasks about recent AI trends or opportunity areas, prioritize account timeline sampling over Home feed.

At minimum, the collection pass should:

- read visible tweet and article text
- capture source link, text, author, time, and interaction signals
- filter by account signal and time window first, then use keywords as a secondary filter
- keep only content from the last 7 days
- apply views or engagement thresholds when useful
- deduplicate posts
- stop or refresh when multiple rounds produce no new signal

Search page fallback rules:

- try only one narrow query at a time
- always switch to `Latest`
- if the page shows `Something went wrong. Try reloading.`, click `Retry` only once
- if `Retry` still fails, abandon search and return to timeline sampling immediately

Home feed is suitable only when:

- the user explicitly wants to inspect the discussion inside the followed network
- you need weak but relevant signals that have already entered the account's follow graph

Home feed is not suitable for:

- serving as the primary evidence for broad AI trend conclusions
- filtering by the bare keyword `ai`

### 8. Data fields

Keep at least these fields in the normalized result set:

- `title`
- `text`
- `source`
- `url`
- `author`
- `published_at`
- `score`
- `views`
- `likes`
- `comments`

Suggested ranking priority:

1. official announcement or original source
2. freshness
3. engagement or discussion intensity
4. topic relevance to the user's ask

### 9. Output rules

Default summary structure:

#### Hot Topics

- list the 3 to 5 most important events or discussion threads

#### Viewpoints

- summarize what the community is debating
- distinguish official signals, developer practice, and sentiment-heavy takes

#### Opportunities

- identify product, content, integration, or workflow opportunities
- clearly label whether a statement is a fact or an inference grounded in facts

#### One-page summary

If the user asks for a summary, a one-pager, an executive version, or a short brief with links, generate an additional Markdown summary.

Recommended output path:

- `survey/summary-one-page.md`

Rules for the one-page summary:

- keep it close to one page
- focus on the 3 to 5 highest-value conclusions
- attach 1 to 2 supporting source samples to each conclusion whenever possible
- always include a `Reference Links` section
- prefer original X post links; if a post points to an official blog, include that blog link too
- if a conclusion relies on a previously validated sample that did not reproduce cleanly in the rerun, mark it as `historical sample`
- do not present unsupported judgments as hard facts
- for an executive audience, prioritize:
  - conclusion
  - why it matters now
  - opportunity area
  - reference links

## Execution Learnings

### Confirmed wins

- `validate_x_cookies.py` reliably separates `file exists` from `still logs in`
- a valid cookie already present on the same machine can usually be reused without a fresh login
- X profile timelines are far more stable and higher-signal than Home feed or search
- official accounts plus top ecosystem accounts are enough to draft a strong trend or opportunity brief quickly
- starting with official moves, then checking researchers and tooling accounts, then confirming against public news sources leads to more stable conclusions

### Confirmed failure modes

- Home feed is noisy, especially when filtered with short terms such as `ai`
- the X search page often fails with `Something went wrong. Try reloading.` and cannot be the main path
- relying on a single path is brittle; the fallback order must be explicit
- some profile pages may fail with `net::ERR_ABORTED`; retry once and then skip
- conclusions without stable source links should not be framed as certain facts

## X Collection Recommendations

### Recommended

- use `validated cookie + high-signal account timelines + agent-browser navigation`
- read the account list directly from `references/x-high-signal-accounts.md`
- load keywords from a file when available instead of hardcoding them
- collect the first 10 to 20 high-signal samples before cross-checking with public news sources
- prioritize extracting:
  - text
  - time
  - username or display name
  - original post link
  - engagement metrics
- use native `agent-browser` commands such as `open`, `snapshot`, `scroll`, `click`, and `get`
- sample 2 to 3 official accounts first, then 1 to 2 core people, then 2 to 3 ecosystem accounts
- avoid scanning the extended observation group by default
- skip the search page entirely when timeline samples already support the conclusion
- generate a one-page summary with reference links when the user wants a stable sharable brief
- generate an HTML report with `scripts/generate_report.py` and `scripts/template.html` when the user wants a shareable artifact

### Not recommended

- do not depend on `collect_x_feed.py` as the default route
- do not write temporary Python or JavaScript scraping scripts just for X
- do not use the search page as the default starting point
- do not use Home feed as the only factual source
- do not use the bare word `ai` as the main Home feed filter
- do not use one extremely long OR query as the main search strategy
- do not rely on Nitter
- do not collect X content in a logged-out state
- do not treat pure marketing noise as a core conclusion
- do not draw conclusions from a single viral post alone

## Report Files

If the user wants an HTML report, use:

- `scripts/generate_report.py`
- `scripts/template.html`

Recommended JSON structure:

```json
{
  "title": "Last 7 Days AI and X Discussion Brief",
  "config": {
    "keywords": ["chatgpt", "claude", "mcp"],
    "daysAgo": 7,
    "sources": ["web", "hn", "github", "x"]
  },
  "items": [
    {
      "title": "Claude capability updates spread quickly in the community",
      "text": "Developers on X are actively discussing Claude's changes in coding and agent workflows.",
      "source": "X",
      "url": "https://example.com",
      "author": "@example",
      "published_at": "2026-04-12T08:30:00Z",
      "score": 90,
      "views": 180000,
      "likes": 2400,
      "comments": 180
    }
  ]
}
```

## Quick Decisions

- if the user wants a fast overview: search public news first, then sample 3 to 6 high-signal accounts
- if the user explicitly wants X sentiment: use `validated cookie + high-signal timelines + agent-browser navigation`
- if the user wants a recent 7-day brief: default to `Hot Topics / Viewpoints / Opportunities`
- if the user wants a one-pager or a linked summary: generate `survey/summary-one-page.md`
- if the user wants something shareable: generate an HTML report
- if the `/tmp` cookie is missing or invalid: refresh login and export a new cookie
- if high-signal account coverage is already sufficient: do not touch the search page
- if the stable group is sufficient but the user wants wider coverage: ask whether to expand into `model company / AI coding / Chinese community`
- if Home feed becomes too noisy: stop scrolling and return to account timelines
- if the search page errors: retry once, then immediately fall back to account timelines
