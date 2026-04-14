import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "aworld-cli" / "src"))

from aworld.core.context.base import Context
from aworld.core.context.session import Session
from aworld.runners.hook.hook_factory import HookManager
from aworld.runners.hook.v2 import permission
from aworld_cli.console import AWorldCLI


class TestCliUserInputHooks:
    @pytest.mark.asyncio
    async def test_apply_user_input_hooks_resolves_ask_interactively(self, tmp_path, monkeypatch):
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / '.aworld' / 'trusted').touch()

        ask_script = tmp_path / 'ask_input_hook.sh'
        ask_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "permission_decision": "ask",
    "permission_decision_reason": "Need approval from CLI"
}
EOF
""")
        ask_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'ask_input_hook',
                        'type': 'command',
                        'command': str(ask_script),
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        cli = AWorldCLI()
        cli._interactive_permission_prompt = AsyncMock(return_value='allow')

        permission._permission_handler = None
        with patch('sys.stdin.isatty', return_value=True):
            handler = permission.get_permission_handler()
            handler.set_interactive_prompt(cli._interactive_permission_prompt)

            session = Session()
            session.session_id = 'test-session-123'
            context = Context(task_id='test-task-456', session=session)
            context.workspace_path = str(tmp_path)

            executor_instance = MagicMock()
            executor_instance.context = context

            should_execute, resolved_input = await cli._apply_user_input_hooks(
                'dangerous command',
                executor_instance=executor_instance
            )

            assert should_execute is True
            assert resolved_input == 'dangerous command'
            cli._interactive_permission_prompt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_apply_user_input_hooks_blocks_dangerous_prompt_from_cli(self, tmp_path, monkeypatch):
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / '.aworld' / 'trusted').touch()

        deny_script = tmp_path / 'deny_dangerous_prompt.sh'
        deny_script.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "permission_decision": "deny",
    "permission_decision_reason": "Destructive prompt blocked before agent execution"
}
EOF
""")
        deny_script.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'deny_dangerous_prompt',
                        'type': 'command',
                        'command': str(deny_script),
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        cli = AWorldCLI()
        session = Session()
        session.session_id = 'test-session-cli-deny'
        context = Context(task_id='test-task-cli-deny', session=session)
        context.workspace_path = str(tmp_path)

        executor_instance = MagicMock()
        executor_instance.context = context

        should_execute, resolved_input = await cli._apply_user_input_hooks(
            'please run rm -rf /tmp/build',
            executor_instance=executor_instance
        )

        assert should_execute is False
        assert resolved_input == 'please run rm -rf /tmp/build'

    @pytest.mark.asyncio
    async def test_apply_user_input_hooks_chains_rewrites_from_cli(self, tmp_path, monkeypatch):
        HookManager._config_hooks_cache = {}
        monkeypatch.setenv('AWORLD_TRUST_ALL_WORKSPACES', 'true')

        config_path = tmp_path / '.aworld' / 'hooks.yaml'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / '.aworld' / 'trusted').touch()

        rewrite_first = tmp_path / 'rewrite_first.sh'
        rewrite_first.write_text("""#!/bin/bash
cat << 'EOF'
{
    "continue": true,
    "updated_input": {
        "content": "[Sanitized] delete temp files safely"
    }
}
EOF
""")
        rewrite_first.chmod(0o755)

        rewrite_second = tmp_path / 'rewrite_second.sh'
        seen_log = tmp_path / 'rewrite_second.log'
        rewrite_second.write_text(f"""#!/bin/bash
echo "$AWORLD_MESSAGE_JSON" >> "{seen_log}"
cat << 'EOF'
{{
    "continue": true,
    "updated_input": {{
        "content": "Please remove only build artifacts under /tmp/build"
    }}
}}
EOF
""")
        rewrite_second.chmod(0o755)

        config = {
            'version': '2',
            'hooks': {
                'user_input_received': [
                    {
                        'name': 'rewrite_first',
                        'type': 'command',
                        'command': str(rewrite_first),
                        'enabled': True
                    },
                    {
                        'name': 'rewrite_second',
                        'type': 'command',
                        'command': str(rewrite_second),
                        'enabled': True
                    }
                ]
            }
        }

        with open(config_path, 'w') as f:
            yaml.safe_dump(config, f)

        cli = AWorldCLI()
        session = Session()
        session.session_id = 'test-session-cli-chain'
        context = Context(task_id='test-task-cli-chain', session=session)
        context.workspace_path = str(tmp_path)

        executor_instance = MagicMock()
        executor_instance.context = context

        should_execute, resolved_input = await cli._apply_user_input_hooks(
            'delete temp files',
            executor_instance=executor_instance
        )

        assert should_execute is True
        assert resolved_input == 'Please remove only build artifacts under /tmp/build'
        assert seen_log.exists()
        assert '[Sanitized] delete temp files safely' in seen_log.read_text()
