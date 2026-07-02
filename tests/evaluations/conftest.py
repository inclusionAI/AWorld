from __future__ import annotations


def pytest_addoption(parser):
    group = parser.getgroup("trajectory evaluator")
    group.addoption(
        "--task-id",
        "--task_id",
        action="store",
        dest="trajectory_task_id",
        default=None,
        help="Task id to replay from the trajectory log.",
    )
    group.addoption(
        "--trajectory-log",
        "--trajectory_log",
        action="store",
        dest="trajectory_log",
        default=None,
        help="Path to the trajectory log used by the manual replay test.",
    )
    group.addoption(
        "--agent-prompt",
        "--agent_prompt",
        action="store",
        dest="trajectory_agent_prompt",
        default=None,
        help="Path to the trajectory evaluator agent.md prompt.",
    )
    group.addoption(
        "--out-dir",
        "--out_dir",
        action="store",
        dest="trajectory_out_dir",
        default=None,
        help="Directory for extracted trajectory and evaluator report outputs.",
    )
    group.addoption(
        "--judge-timeout",
        "--judge_timeout",
        action="store",
        dest="trajectory_judge_timeout",
        default=None,
        type=float,
        help="Timeout in seconds for the trajectory evaluator judge agent.",
    )
