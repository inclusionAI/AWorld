import argparse
from mcp.server.fastmcp import FastMCP
from mcp_ui_server import create_ui_resource
from mcp_ui_server.core import UIResource

# Create FastMCP instance
mcp = FastMCP("my-mcp-server")


@mcp.tool()
def greet() -> list[UIResource]:
    """A simple greeting tool that returns a UI resource."""
    ui_resource = create_ui_resource({
        "uri": "ui://greeting/simple",
        "content": {
            "type": "rawHtml",
            "htmlString": """
                <div style="padding: 20px; text-align: center; font-family: Arial, sans-serif;">
                    <h1 style="color: #2563eb;">Hello from Python MCP Server!</h1>
                    <p>This UI resource was generated server-side using mcp-ui-server.</p>
                </div>
            """
        },
        "encoding": "text"
    })
    return [ui_resource]


@mcp.tool()
def show_dashboard() -> list[UIResource]:
    """Display a sample dashboard with metrics."""
    dashboard_html = """
    <div style="padding: 20px; font-family: Arial, sans-serif;">
        <h1>Server Dashboard</h1>
        <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-top: 20px;">
            <div style="background: #f0f9ff; border: 1px solid #0ea5e9; border-radius: 8px; padding: 16px;">
                <h3 style="margin-top: 0; color: #0369a1;">Active Connections</h3>
                <p style="font-size: 24px; font-weight: bold; margin: 0; color: #0c4a6e;">42</p>
            </div>
            <div style="background: #f0fdf4; border: 1px solid #22c55e; border-radius: 8px; padding: 16px;">
                <h3 style="margin-top: 0; color: #15803d;">CPU Usage</h3>
                <p style="font-size: 24px; font-weight: bold; margin: 0; color: #14532d;">23%</p>
            </div>
        </div>
    </div>
    """

    ui_resource = create_ui_resource({
        "uri": "ui://dashboard/main",
        "content": {
            "type": "rawHtml",
            "htmlString": dashboard_html
        },
        "encoding": "text"
    })
    return [ui_resource]


@mcp.tool()
def show_external_site() -> list[UIResource]:
    """Display an external website in an iframe."""
    ui_resource = create_ui_resource({
        "uri": "ui://external/example",
        "content": {
            "type": "externalUrl",
            "iframeUrl": "https://example.com"
        },
        "encoding": "text"
    })
    return [ui_resource]


@mcp.tool()
def show_interactive_demo() -> list[UIResource]:
    """Show an interactive demo with buttons that send intents."""
    interactive_html = """
    <div style="padding: 20px; font-family: Arial, sans-serif;">
        <h2>Interactive Demo</h2>
        <p>Click the button below to send an intent back to the parent:</p>

        <button onclick="sendIntent()" 
                style="background: #2563eb; color: white; padding: 10px 20px; border: none; border-radius: 4px; cursor: pointer;">
            Send Intent
        </button>

        <div id="status" style="margin-top: 20px; padding: 10px; background: #f3f4f6; border-radius: 4px;">
            Ready
        </div>
    </div>

    <script>
        function sendIntent() {
            const status = document.getElementById('status');
            status.innerHTML = 'Intent sent!';

            if (window.parent) {
                window.parent.postMessage({
                    type: 'intent',
                    payload: {
                        intent: 'demo_interaction',
                        params: { source: 'python-server', timestamp: new Date().toISOString() }
                    }
                }, '*');
            }
        }
    </script>
    """

    ui_resource = create_ui_resource({
        "uri": "ui://demo/interactive",
        "content": {
            "type": "rawHtml",
            "htmlString": interactive_html
        },
        "encoding": "text"
    })
    return [ui_resource]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="My MCP Server")
    parser.add_argument("--http", action="store_true", help="Use HTTP transport instead of stdio")
    parser.add_argument("--port", type=int, default=3000, help="Port for HTTP transport (default: 3000)")
    args = parser.parse_args()

    if args.http:
        print("🚀 Starting MCP server on HTTP (SSE transport)")
        print("📡 Server will use SSE transport settings")
        mcp.settings.port = args.port
        mcp.run(transport="sse")
    else:
        print("🚀 Starting MCP server with stdio transport")
        mcp.run()