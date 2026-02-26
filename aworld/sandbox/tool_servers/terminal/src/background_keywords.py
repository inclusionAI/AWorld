"""
Keyword list for background / long-running processes.

If a command contains any of the keywords below, it is treated as a
potentially background or long-running process and the server will
capture output via a temporary file to avoid PIPE blocking / timeouts.
"""

# Matching is done with simple `in` checks; it is recommended to include
# spaces or clear boundaries in keywords to reduce false positives.
LONG_RUNNING_KEYWORDS: list[str] = [
    # Process managers
    "nohup ",
    "pm2 ",
    "forever ",
    "supervisord",
    "supervisorctl",
    # Node / frontend
    "npm run dev",
    "npm start",
    "yarn dev",
    "yarn start",
    "pnpm dev",
    "vite dev",
    "next dev",
    "nuxt dev",
    "webpack-dev-server",
    "vue-cli-service serve",
    # Python web
    "uvicorn ",
    "gunicorn ",
    "flask run",
    "runserver",
    "python -m http.server",
    # Containers / orchestration
    "docker compose up",
    "docker-compose up",
    "docker run ",
    "kubectl port-forward",
    "kubectl proxy",
    # Logs / monitoring
    "tail -f",
    "tail -F",
    "watch ",
    "journalctl -f",
]
