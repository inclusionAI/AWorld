# context_stat – Digest Logger Analysis

Analyze AWorld digest logs (context stats, tree, trend & comparison charts).  
Tool: `aworld/logs/tools/context_stat_tool.py`.

**run.py** – Set `log_file`, then `stat_log(log_file)`:

```python
log_file = "/path/to/digest_logger.log"   # or use AWORLD_DIGEST_LOG
stat_log(log_file)                        # tree + trend + compare (first session)
stat_log(log_file, list_only=True)        # list sessions only
```

Run (from repo root so `aworld` is importable):

```bash
python examples/aworld_quick_start/context_stat/run.py
python examples/aworld_quick_start/context_stat/run.py --list
```

**Full CLI** (any path, session, output dir):

```bash
python aworld/logs/tools/context_stat_tool.py /path/to/digest.log --list
python aworld/logs/tools/context_stat_tool.py /path/to/digest.log --session-id ID --tree --trend --output-dir ./out
python aworld/logs/tools/context_stat_tool.py --help
```

**API:** `stat_log(log_file, list_only=False, output_dir=".")`. See docstring in `context_stat_tool.py`.
