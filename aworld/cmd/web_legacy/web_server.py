import os
import streamlit.web.bootstrap as bootstrap


def run_web_server(port, args=None, **kwargs):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    kwargs = {**kwargs, "server.port": port}
    bootstrap.load_config_options(flag_options=kwargs)
    bootstrap.run(script, False, args, flag_options=kwargs)