# Bundled Skill Exposure Policy

`aworld-skills` is a source catalog, not a promise that every bundled Skill is
appropriate for every task. A Skill is enabled by default only when its workflow
and dependencies are broadly applicable across AWorld CLI environments.

Specialized Skills declare this in their `SKILL.md` front matter:

```yaml
default_enabled: false
```

The runtime keeps those Skills out of automatic discovery and activation. They
remain visible in Skill management commands and can be selected explicitly for
one run or enabled persistently by the user:

```text
aworld-cli run --skill <skill-name> ...
aworld-cli skill enable <skill-name>
```

An explicit user disable always takes precedence. Skills without the field retain
the historical default-enabled behavior, including user-installed and workspace
Skills, so this policy is backward compatible.

The specialized video, media, app evaluation, agent generation, optimization,
and self-evolve workflows are disabled by default. `agent-browser` remains the
general-purpose default in this source catalog. FileX retains its separate
explicit activation policy. There is no separate built-in AI for Science skill.
