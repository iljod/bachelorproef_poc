import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from routers import roster, session

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app() -> FastAPI:
    """Build the shared FastAPI app. Both deployments call this; they differ
    only in their entrypoint wiring (e.g. the Docker side also starts a cleanup
    thread, the Kubernetes side leaves that to a CronJob)."""
    app = FastAPI(
        title="Lab Provisioning API",
        description="Start/stop student lab sessions and generate Guacamole token links.",
    )

    app.include_router(roster.router,  prefix="/roster")
    app.include_router(session.router, prefix="/session")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    return app
