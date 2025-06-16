import click


from .web import web_server
from .api_server import api_server


@click.group()
def main(*args, **kwargs):
    print(
        """\
    AWorld CLI Help:
    aworld web: run aworld web agent
    aworld api_server: run aworld api server
    aworld help: show help"""
    )


@main.command("web")
@click.option(
    "--port", type=int, default=8000, help="Port to run the AWorld agent web app"
)
@click.argument("args", nargs=-1)
def main_web(port, args=None, **kwargs):
    web_server.run_web_server(port, args, **kwargs)


@main.command("api_server")
@click.option(
    "--port", type=int, default=8000, help="Port to run the AWorld api server"
)
@click.argument("args", nargs=-1)
def main_api(port, args=None, **kwargs):
    api_server.run_api_server(port, args, **kwargs)


if __name__ == "__main__":
    main()
