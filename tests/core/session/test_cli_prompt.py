"""Host discovery boundaries and fidelity of the prompt sent to the model."""
import asyncio
from hashlib import sha256
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from aworld.cli.main import _host, _result_output_projection, parser
from aworld.cli.prompt import discover_skills, workspace_prompt
from aworld.cli.trajectory import build_trajectory
from aworld.core.agent.messages import AssistantMessage, ToolCall
from aworld.core.session import RunError, RunResult, RunStatus, RunStopReason


def skill(root, name, description='metadata', body='BODY_ONLY_WHEN_READ'):
    path = root / name / 'SKILL.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'---\nname: {name}\ndescription: {description}\n---\n{body}\n')
    return path


def test_discovery_precedence_and_explicit_isolation(tmp_path):
    pytest.importorskip('yaml')
    project, home, explicit = [tmp_path / name for name in ('project', 'home', 'explicit')]
    project.mkdir()
    (project / '.git').mkdir()
    nested = project / 'src'
    nested.mkdir()
    skill(home / '.agents/skills', 'same', 'user')
    skill(project / '.agent/skills', 'same', 'project')
    path = skill(nested / '.agents/skills', 'same', 'nested')
    skill(home / '.agent/skills', 'user-only')
    assert [(s.name, s.description) for s in discover_skills(nested, home=home)] == [('same', 'nested'), ('user-only', 'metadata')]
    assert discover_skills(nested, home=home)[0].location == str(path)
    skill(explicit, 'bench')
    assert [s.name for s in discover_skills(nested, paths=[explicit], home=home)] == ['bench']
    assert discover_skills(nested, disabled=True, home=home) == ()
    with pytest.raises(NotADirectoryError):
        discover_skills(nested, paths=[tmp_path / 'missing'])


def test_minimal_cli_explicitly_selects_multiple_skills_in_requested_order(tmp_path):
    pytest.importorskip('yaml')
    skills = tmp_path / 'skills'
    first = skill(skills, 'first-skill')
    second = skill(skills, 'second-skill')
    unused = skill(skills, 'unused-skill')
    requests = []

    class Model:
        async def complete(self, request):
            requests.append(request)
            return AssistantMessage('done')

    args = parser().parse_args([
        '--demo', '--cwd', str(tmp_path), '--task', 'test',
        '--skill-path', str(skills), '--skill', 'second-skill',
        '--skill', 'first-skill',
        '--trajectory-output', str(tmp_path / 'trajectory.json'),
        '--result-output', str(tmp_path / 'result.json'),
    ])
    with patch('aworld.cli.main.DemoModel', Model):
        assert asyncio.run(_host(args, 'run')) == 0

    prompt = requests[0].system_prompt
    assert prompt.index(str(second)) < prompt.index(str(first))
    assert str(unused) not in prompt
    trajectory = json.loads((tmp_path / 'trajectory.json').read_text())
    result = json.loads((tmp_path / 'result.json').read_text())
    assert trajectory['agent']['extra']['skills'] == [
        {'name': 'second-skill', 'location': str(second)},
        {'name': 'first-skill', 'location': str(first)},
    ]
    assert [
        item['skill_name']
        for item in trajectory['agent']['extra']['skill_activation_evidence']
    ] == ['second-skill', 'first-skill']
    assert all(
        item['package_fingerprint'].startswith('sha256:')
        for item in trajectory['agent']['extra']['skill_activation_evidence']
    )
    assert result['skill_activation_evidence'] == (
        trajectory['agent']['extra']['skill_activation_evidence']
    )


def test_host_discovery_resolves_installed_skill_aliases(tmp_path):
    pytest.importorskip("yaml")
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    manifest = skill(home / "installed", "linked-skill")
    alias_root = home / ".agents/skills"
    alias_root.mkdir(parents=True)
    (alias_root / "linked-skill").symlink_to(manifest.parent, target_is_directory=True)
    discovered = discover_skills(project, home=home)
    assert len(discovered) == 1
    assert discovered[0].location == str(manifest.resolve())
    assert discovered[0].package_fingerprint.startswith("sha256:")
    with pytest.raises(ValueError, match="symlink"):
        discover_skills(project, paths=[alias_root], home=home)


def test_minimal_cli_rejects_unavailable_explicit_skill(tmp_path):
    pytest.importorskip('yaml')
    skills = tmp_path / 'skills'
    skill(skills, 'available-skill')
    args = parser().parse_args([
        '--demo', '--cwd', str(tmp_path), '--task', 'test',
        '--skill-path', str(skills), '--skill', 'missing-skill',
    ])

    with pytest.raises(ValueError, match='Requested skill is not available'):
        asyncio.run(_host(args, 'run'))


@pytest.mark.parametrize(
    ('status', 'reason', 'error'),
    [
        (RunStatus.COMPLETED, RunStopReason.END_TURN, None),
        (
            RunStatus.FAILED,
            RunStopReason.DEADLINE_EXCEEDED,
            RunError('deadline', 'expired'),
        ),
        (RunStatus.CANCELLED, RunStopReason.CANCELLED, None),
    ],
)
def test_minimal_atif_keeps_skill_attestation_for_every_terminal_status(
    tmp_path, status, reason, error
):
    pytest.importorskip('yaml')
    root = tmp_path / 'skills'
    skill(root, 'task-skill')
    selected = discover_skills(tmp_path, paths=[root])
    result = RunResult('run', 'session', status, reason, None, error)

    trajectory = build_trajectory(
        (),
        result=result,
        agent=SimpleNamespace(skills=selected, tools=()),
    )

    evidence = trajectory['agent']['extra']['skill_activation_evidence']
    result_projection = _result_output_projection(
        result,
        SimpleNamespace(skills=selected),
    )
    assert len(evidence) == 1
    assert evidence[0]['skill_name'] == 'task-skill'
    assert evidence[0]['package_fingerprint'].startswith('sha256:')
    assert result_projection['skill_activation_evidence'] == evidence
    assert result_projection['status'] == status
    assert result_projection['stop_reason'] == reason


def test_workspace_rules_bounded_and_scoped(tmp_path):
    (tmp_path / 'AGENTS.md').write_text('outside')
    project = tmp_path / 'project'
    project.mkdir()
    (project / '.git').mkdir()
    (project / 'AGENTS.md').write_text('root-rule')
    nested = project / 'src'
    nested.mkdir()
    (nested / 'AGENTS.md').write_text('scoped-rule' * 2000)
    text, sources = workspace_prompt(nested)
    assert 'outside' not in text
    assert text.index('root-rule') < text.index('scoped-rule')
    assert sources[-1]['truncated'] is True
    assert len(text) < 17000


def test_actual_prompt_skill_body_and_runtime_are_exported(tmp_path):
    pytest.importorskip('yaml')
    (tmp_path / 'AGENTS.md').write_text('Use verified local evidence.')
    path = skill(tmp_path / '.agents/skills', 'compute')
    requests = []

    class Model:
        async def complete(self, request):
            requests.append(request)
            if len(requests) == 1:
                return AssistantMessage(tool_calls=(ToolCall('read-skill', 'read', {'path': str(path)}),))
            return AssistantMessage('verified')

    args = parser().parse_args(['--demo', '--cwd', str(tmp_path), '--task', 'test', '--timeout', '10',
        '--network-policy', 'allowed', '--trajectory-output', str(tmp_path / 'trajectory.json')])
    with patch('aworld.cli.main.DemoModel', Model), patch('pathlib.Path.home', return_value=tmp_path / 'empty-home'):
        assert asyncio.run(_host(args, 'run')) == 0
    trajectory = json.loads((tmp_path / 'trajectory.json').read_text())
    systems = [s for s in trajectory['steps'] if s['source'] == 'system']
    assert len(systems) == len(requests) == 2
    for step, request in zip(systems, requests):
        assert step['message'] == request.system_prompt
        assert step['extra']['sha256'] == sha256(request.system_prompt.encode()).hexdigest()
        assert step['extra']['version'] == 'aworld-system-v2'
        assert step['extra']['sources'][1]['path'] == str(tmp_path / 'AGENTS.md')
    first = requests[0].system_prompt
    assert str(path) in first and 'BODY_ONLY_WHEN_READ' not in first
    assert 'Use verified local evidence.' in first
    state = json.loads(first.split('Runtime context (host facts; null means unspecified):\n')[1])
    assert state['session_id'] == trajectory['session_id']
    assert state['run_id'] == trajectory['extra']['run_id']
    assert 0 < state['remaining_budget_seconds'] <= 10
    assert state['network'] == 'allowed'
    assert state['work_file'].endswith('/' + trajectory['session_id'] + '/WORK.md')
    assert 'BODY_ONLY_WHEN_READ' in str(requests[1].messages)
