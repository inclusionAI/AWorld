import click
from .web_legacy import web_server as legacy_web_server
from .web import web_server, api_server


@click.group()
def main(*args, **kwargs):
    print(
        """\
    AWorld CLI Help:
        aworld web: run aworld web ui server
        aworld api: run aworld api server
        aworld web_legacy: run aworld web agent (legacy), Streamlit Web UI
        aworld help: show help"""
    )


@main.command("web")
@click.option(
    "--port", type=int, default=8000, help="Port to run the AWorld api server"
)
@click.argument("args", nargs=-1)
def main_web(port, args=None, **kwargs):
    web_server.run_server(port, args, **kwargs)


@main.command("api")
@click.option(
    "--port", type=int, default=8000, help="Port to run the AWorld api server"
)
@click.argument("args", nargs=-1)
def main_api(port, args=None, **kwargs):
    api_server.run_server(port, args, **kwargs)


@main.command("web_legacy")
@click.option(
    "--port", type=int, default=8000, help="Port to run the AWorld agent web app"
)
@click.argument("args", nargs=-1)
def main_web_legacy(port, args=None, **kwargs):
    legacy_web_server.run_web_server(port, args, **kwargs)

if __name__ == "__main__":
    main()
