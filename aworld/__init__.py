# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import atexit
import os

# Try to load .env file if python-dotenv is available
# This is optional and should not fail if the package is not installed yet (e.g., during pip install)
try:
    from dotenv import load_dotenv

    success = load_dotenv()
    if not success:
        load_dotenv(os.path.join(os.getcwd(), ".env"))
except Exception as e:
    # Log other errors but don't fail initialization
    print(f"Warning: Failed to load .env file: {e}")


def cleanup():
    import re

    try:
        value = os.environ.get("LOCAL_TOOLS_ENV_VAR", '')
        if value:
            for action_file in value.split(";"):
                v = re.split(r"\w{6}__tmp", action_file)[0]
                if v == action_file:
                    continue
                tool_file = action_file.replace("_action.py", ".py")
                try:
                    os.remove(action_file)
                    os.remove(tool_file)
                except:
                    pass
    except:
        pass
    os.environ["LOCAL_TOOLS_ENV_VAR"] = ''


atexit.register(cleanup, )
