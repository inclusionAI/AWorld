# Packaging and CLI

The root `aworld` wheel includes the Session/Run kernel, Agent, Context, Tools, optional model adapter, CLI and ATIF export. `aworld` remains the Python import and command. There is no `aworldv1` Python package. `aworldv1` is the separate Runtime Harness identifier.

Hatchling reads a literal version from `aworld/_version.py`. Builds neither import business modules nor invoke pip; default dependencies are empty. Install `.[llm,skills]` to use the existing OpenAIProvider through the Model protocol adapter and discover YAML Skill metadata. `aworld-cli` is an optional delegating wrapper depending on this same root wheel.

```bash
python scripts/build_packages.py
python -m pip install '.[llm,skills]'
aworld run --demo --task hello --follow-up again --json
aworld run --model MODEL --base-url URL --task 'Read the latest earnings' \
  --skill-path ~/.agents/skills --trajectory-output trajectory.json --result-output result.json
```

Pass keys via AWORLD_API_KEY / OPENAI_API_KEY. `--events` writes observation events to stderr, `--json` writes terminal RunResult to stdout. ATIF export includes model/tool interactions and Session/Run identity; unavailable token usage is explicitly marked, never estimated. Terminal artifacts are written atomically on completion, failure and cancellation.

The CLI uses the same direct Agent loop as applications. Session history persists for follow-up Runs in this process. Interactive commands `/new`, `/sessions`, `/session ID`, `/query JSON` and `/read ID` operate on the current in-process store. Cross-process persistence and automatic compaction remain future work.

The Micron integration test used the local search-api Skill and a real model. It searched the release, read the complete official press-release syndication after IR returned HTTP 403, and produced a report. Runtime integration is tested separately with a pinned aworld wheel, native ATIF and SkillsBench verifier output.

`build_packages.py` builds both projects in one invocation, checks that CLI and core versions and dependencies agree, and writes `packages.json` with wheel/sdist SHA256 values. The Runtime bundle installs the two wheels together.

Historical code, CLI, resources and tests are retained in the repository. Wheels explicitly include the new kernel and the existing provider dependency closure with local tokenizer resources. Default kernel/demo imports remain lazy; live models opt into the provider dependencies through `llm`. Historical source remains in sdists for review; cleanup will happen gradually after the new version stabilizes.
