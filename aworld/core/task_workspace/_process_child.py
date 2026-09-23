"""Small stdlib-only resource boundary, executed in a fresh child interpreter."""

import json
import os
import sys


def main():
    limits = json.loads(sys.argv[1])
    if os.name == "posix":
        import resource

        requested = {
            resource.RLIMIT_CPU: limits["cpu_seconds"],
            resource.RLIMIT_FSIZE: limits["file_bytes"],
            resource.RLIMIT_NOFILE: limits["open_files"],
        }
        if sys.platform.startswith("linux"):
            requested[resource.RLIMIT_AS] = limits["memory_bytes"]
        for kind, value in requested.items():
            _, hard = resource.getrlimit(kind)
            value = min(value, hard) if hard != resource.RLIM_INFINITY else value
            resource.setrlimit(kind, (value, value))
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)


if __name__ == "__main__":
    main()
