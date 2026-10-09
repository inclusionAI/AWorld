"""Host discovery boundaries and fidelity of the prompt sent to the model."""
import asyncio
from hashlib import sha256
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aworld.cli.main import _host, parser
from aworld.cli.prompt import discover_skills, workspace_prompt
from aworld.core.agent.messages import AssistantMessage, ToolCall


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
