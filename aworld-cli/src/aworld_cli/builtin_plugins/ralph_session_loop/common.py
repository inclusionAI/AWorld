import argparse
import re
import shlex
from datetime import datetime, timezone


class RalphArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse_loop_args(user_args: str) -> dict:
    parser = RalphArgumentParser(prog="/ralph-loop", add_help=False)
    parser.add_argument("prompt_parts", nargs="*")
    parser.add_argument("--verify", action="append", dest="verify_commands", default=[])
    parser.add_argument("--completion-promise", dest="completion_promise")
    parser.add_argument("--max-iterations", dest="max_iterations", type=int)

    tokens = shlex.split(user_args or "")
    namespace = parser.parse_args(tokens)
    prompt = " ".join(namespace.prompt_parts).strip()
    if not prompt:
        raise ValueError("missing task prompt")
    if namespace.max_iterations is not None and namespace.max_iterations < 1:
        raise ValueError("--max-iterations must be >= 1")

    return {
        "prompt": prompt,
        "verify_commands": [str(item).strip() for item in namespace.verify_commands if str(item).strip()],
        "completion_promise": (namespace.completion_promise or "").strip() or None,
        "max_iterations": namespace.max_iterations,
    }


def started_at_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_loop_prompt(prompt: str, verify_commands: list[str] | None, completion_promise: str | None) -> str:
    verify_commands = list(verify_commands or [])

    lines = ["Task:", prompt.strip(), ""]
    lines.append("Verification requirements:")
    if verify_commands:
        for index, command in enumerate(verify_commands, start=1):
            lines.append(f"{index}. Run: {command}")
    else:
        lines.append("1. No explicit verification commands were declared.")
    lines.append("")
    lines.append("Completion rule:")
    if completion_promise:
        lines.append(
            f"Only output <promise>{completion_promise}</promise> when every verification requirement passes."
        )
        lines.append("If verification fails, fix the issue and continue iterating.")
    else:
        lines.append("No completion promise is set for this loop.")
        lines.append("Continue iterating until the work is done or the operator cancels the loop.")
    return "\n".join(lines).strip()


def extract_completion_promise(answer: str | None) -> str | None:
    if not answer:
        return None
    match = re.search(r"<promise>(.*?)</promise>", answer, flags=re.DOTALL)
    if not match:
        return None
    return match.group(1).strip()


def summarize_text(text: str | None, limit: int = 160) -> str | None:
    if text is None:
        return None
    normalized = " ".join(str(text).split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."
