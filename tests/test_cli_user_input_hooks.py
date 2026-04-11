import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "aworld-cli" / "src"))

from aworld.core.context.base import Context
from aworld.core.context.session import Session
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
