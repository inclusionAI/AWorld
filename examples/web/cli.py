import os
import streamlit.web.bootstrap as bootstrap


def _main(*args, **kwargs):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    bootstrap.run(script, False, args[1:], flag_options=kwargs)


if __name__ == "__main__":
    _main()
