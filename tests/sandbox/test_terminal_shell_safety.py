from __future__ import annotations

import ast
import os
import re
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import bashlex
import pytest
from bashlex import ast as shell_ast
from bashlex import flags as shell_flags
from bashlex import parser as shell_parser
from bashlex import subst as shell_subst
from bashlex import tokenizer as shell_tokenizer
from bashlex import utils as shell_utils

ROOT = Path(__file__).resolve().parents[2]
TERMINAL = ROOT / "aworld/sandbox/tool_servers/terminal/src/terminal.py"


@pytest.fixture(scope="module")
def terminal():
    # Compile the actual safety implementation without starting an MCP server
    # or importing unrelated AWorld runtime dependencies into the test runner.
    names = {
        "_HeredocRedirects",
        "_SafetyShellTokenizer",
        "_shell_child_nodes",
        "_parse_shell_nodes",
        "_heredoc_expansions",
        "_shell_reads_stdin",
        "_has_heredoc",
        "_shlex_command_segments",
        "_shell_command_segments",
        "_command_words",
        "_is_broad_rm_target",
        "_rm_recurses",
        "_rm_targets",
        "_dangerous_device_output",
        "_check_command_safety",
    }
    tree = ast.parse(TERMINAL.read_text(encoding="utf-8"))
    definitions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names
    ]
    assert {node.name for node in definitions} == names
    namespace = {
        "Path": Path,
        "os": os,
        "re": re,
        "shlex": shlex,
        "bashlex": bashlex,
        "shell_ast": shell_ast,
        "shell_flags": shell_flags,
        "shell_parser": shell_parser,
        "shell_subst": shell_subst,
        "shell_tokenizer": shell_tokenizer,
        "shell_utils": shell_utils,
    }
    exec(  # noqa: S102 - compile only the checked source definitions, never test shell commands.
        compile(ast.Module(definitions, type_ignores=[]), str(TERMINAL), "exec"),
        namespace,
    )
    return SimpleNamespace(**{name: namespace[name] for name in names})


def test_shell_parser_contract_is_owned_by_aworld():
    source = TERMINAL.read_text(encoding="utf-8")
    assert "AWORLD_SHELL_PARSER_VERSION = 1" in source
    assert "RUNTIME_SHELL_PARSER_VERSION" not in source


@pytest.mark.parametrize("delimiter", ["'EOF'", '"EOF"', r"\EOF", "E'O'F", "'E O F'"])
def test_quoted_heredoc_data_is_not_shell_code(terminal, delimiter):
    end = shlex.split(delimiter)[0]
    command = (
        f"cat > output.txt <<{delimiter}\n"
        "Apostrophes in source: n' and don't\n"
        "rm -rf /\n"
        "$(mkfs.ext4 /dev/sda)\n"
        "`dd if=/dev/zero of=/dev/sda`\n"
        ":(){ :|:& };:\n"
        f"{end}\nprintf done\n"
    )
    assert (
        subprocess.run(
            ["bash", "-n"], input=command, text=True, capture_output=True, check=False
        ).returncode
        == 0
    )
    assert terminal._check_command_safety(command) == (True, None)
    assert terminal._shell_command_segments(command) == [["cat"], ["printf", "done"]]


@pytest.mark.parametrize(
    "command",
    [
        "cat <<'EOF'\nsource n'\nEOF\nrm -rf /",
        "cat <<EOF\n$(rm -rf /)\nEOF\n",
        "cat <<EOF\n'$(rm -rf /)'\nEOF\n",
        "cat <<EOF\n`rm -rf /`\nEOF\n",
        "cat <<EOF\n${unused:-$(rm -rf /)}\nEOF\n",
        "cat <<EOF\n$((1 + $(rm -rf /)))\nEOF\n",
        "sh <<'EOF'\nrm -rf /\nEOF\n",
        "bash -s <<'EOF'\nrm -rf /\nEOF\n",
        "{ bash; } <<'EOF'\nrm -rf /\nEOF\n",
        "( /bin/sh ) <<'EOF'\nrm -rf /\nEOF\n",
        "env MODE=test bash <<'EOF'\nrm -rf /\nEOF\n",
        "cat <<'EOF' | bash\nrm -rf /\nEOF\n",
        "cat <<'EOF' | cat | /bin/sh\nrm -rf /\nEOF\n",
        "bash <<'EOF'\nbash -c 'rm -rf /'\nEOF\n",
        "bash <<'EOF'\n:(){ :|:& };:\nEOF\n",
        "time cat <<'EOF'\nliteral n'\nEOF\ntime -p rm -rf /",
        "time -p bash <<'EOF'\nrm -rf /\nEOF\n",
        "time rm -rf /\ncat <<'EOF'\nliteral n'\nEOF\n",
        "echo $(rm -rf /)",
        "X=1 rm --recursive -- /",
        "sudo rm -Rf /",
        "env X=1 rm -rf '$HOME'",
        "mkfs.ext4 /dev/sda",
        "dd if=/dev/zero of=/dev/sda",
        ":(){ :|:& };:",
    ],
)
def test_executable_destructive_commands_remain_rejected(terminal, command):
    # These are parsed only. Never execute destructive test cases.
    safe, reason = terminal._check_command_safety(command)
    assert not safe
    assert reason


@pytest.mark.parametrize(
    "command",
    [
        "cat <<EOF\nplain n' and don't\nEOF\n",
        "cat <<EOF\n$(printf safe)\nEOF\n",
        "cat <<EOF\n`printf safe`\nEOF\n",
        r"cat <<EOF" + "\n" + r"\$(rm -rf /)" + "\nEOF\n",
        "cat <<EOF\n${HOME}\nEOF\n",
        "bash <<'EOF'\nprintf '%s' safe\nEOF\n",
        "bash <<'EOF'\ncat <<'DATA'\nrm -rf /\nDATA\nEOF\n",
        "cat <<'A' <<'B'\nsource n'\nA\nsource m'\nB\nprintf done\n",
        "cat <<-'EOF'\n\tsource n'\n\tEOF\nprintf done\n",
        "cat <<'EOF'\nliteral EOF within a line\nEOF\nprintf done\n",
        "cat <<'EOF'\nsource n'\nEOF\nrm -rf /tmp/scoped-output",
        "printf '%s' 'rm -rf /'",
        "printf '%s' ':(){ :|:& };:'",
        "# rm -rf /\nprintf done",
        "for item in a b; do printf '%s' \"$item\"; done",
        "case x in x) printf safe;; esac",
        "time printf safe",
        "printf '%s' $((1 + 2))",
        "python -c 'print(1 << 3)'",
        "time printf before\ncat <<'EOF'\nsource n'\nEOF\ntime printf after",
        "time -p cat <<'EOF'\nsource n'\nEOF\ntime -p printf after",
        "cat <<'EOF'\ntime rm -rf /\nEOF\ntime printf done",
    ],
)
def test_safe_shell_operations_and_literal_examples_are_allowed(terminal, command):
    assert terminal._check_command_safety(command) == (True, None)


@pytest.mark.parametrize(
    "command",
    [
        "cat <<'EOF'\nsource n'\n",
        "cat <<EOF\nnot terminated\n",
        "cat <<'EOF\nbody\nEOF\n",
        "echo 'unterminated",
        "cat <<EOF\n$(echo unfinished\nEOF\n",
        "cat <<EOF\n`echo unfinished\nEOF\n",
    ],
)
def test_incomplete_commands_fail_closed(terminal, command):
    safe, reason = terminal._check_command_safety(command)
    assert not safe
    assert "parsed safely" in reason


def test_parser_adapter_is_local_and_does_not_patch_bashlex(terminal):
    original_parser = shell_parser._parser
    original_tokenizer = shell_tokenizer.tokenizer
    assert terminal._check_command_safety("cat <<'EOF'\nsource n'\nEOF\n")[0]
    assert shell_parser._parser is original_parser
    assert shell_tokenizer.tokenizer is original_tokenizer
    assert shell_parser._parser("printf safe").redirstack.__class__ is list


def test_source_heredoc_round_trip_in_temporary_directory(terminal, tmp_path):
    content = "const char *message = \"Don't change n'\";\n"
    command = f"cat > source.c <<'SOURCE'\n{content}SOURCE\n"
    assert terminal._check_command_safety(command) == (True, None)
    result = subprocess.run(
        ["bash", "-c", command],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "source.c").read_text() == content
