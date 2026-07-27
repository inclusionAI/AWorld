import argparse
import shlex


GOAL_CONTROL_ACTIONS = {"status", "pause", "clear"}


class GoalArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def resolve_goal_control_action(user_args: str) -> str | None:
    normalized = str(user_args or "").strip().lower()
    if normalized in GOAL_CONTROL_ACTIONS:
        return normalized
    return None


def parse_goal_args(user_args: str) -> dict:
    parser = GoalArgumentParser(prog="/goal", add_help=False)
    parser.add_argument("prompt_parts", nargs="*")
    parser.add_argument("--verify", action="append", dest="verify_commands", default=[])
    parser.add_argument("--completion-promise", dest="completion_promise")
    parser.add_argument("--max-turns", dest="max_turns", type=int)
    parser.add_argument("--from-campaign", dest="from_campaign")

    tokens = shlex.split(user_args or "")
    namespace = parser.parse_args(tokens)
    prompt = " ".join(namespace.prompt_parts).strip()
    from_campaign = (namespace.from_campaign or "").strip() or None
    if from_campaign and (
        prompt or namespace.verify_commands or namespace.completion_promise
    ):
        raise ValueError(
            "--from-campaign cannot be combined with a prompt, --verify, or --completion-promise"
        )
    if not prompt and not from_campaign:
        raise ValueError("missing task prompt")
    if namespace.max_turns is not None and namespace.max_turns < 1:
        raise ValueError("--max-turns must be >= 1")

    return {
        "prompt": prompt,
        "verify_commands": [str(item).strip() for item in namespace.verify_commands if str(item).strip()],
        "completion_promise": (namespace.completion_promise or "").strip() or None,
        "max_turns": namespace.max_turns,
        "from_campaign": from_campaign,
    }
