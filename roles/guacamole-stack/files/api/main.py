from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routers import roster, session
from services.docker_service import start_cleanup_thread

app = FastAPI(
    title="Lab Provisioning API",
    description="Start/stop student container sessions and generate Guacamole token links.",
)

app.include_router(roster.router,  prefix="/roster")
app.include_router(session.router, prefix="/session")
app.mount("/static", StaticFiles(directory="static"), name="static")

start_cleanup_thread()


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse("static/index.html")
