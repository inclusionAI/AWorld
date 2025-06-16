from fastapi import FastAPI
import uvicorn

app = FastAPI()

app.static_files("/static", "static")

def run_api_server(port, args=None, **kwargs):
    print(f"Running API server on port {port}")
    uvicorn.run("api_server:app", host="0.0.0.0", port=port, reload=True)