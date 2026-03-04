#!/usr/bin/env python3
"""Run digest log stats: set log_file, call stat_log(log_file)."""
import glob
import os
import sys
from aworld.logs.tools.context_stat_tool import stat_log


log_file = "yourpath/logs/digest_logger.log"
stat_log(log_file, list_only=("--list" in sys.argv))
