import os
import click
import streamlit.web.bootstrap as bootstrap


@click.group()
def main(*args, **kwargs):
    print(
        """AWorld CLI Help:
    aworld run: run aworld web agent
    aworld help: show help"""
    )


@main.command("run")
@click.argument("args", nargs=-1)
def main_run(args=None, **kwargs):
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
    bootstrap.run(script, False, args[1:], flag_options=kwargs)


if __name__ == "__main__":
    main()
