from webapp import create_app
from config import PLATFORM
from services import orchestrator

app = create_app()

# Docker Compose runs a single API container and reaps its own expired sandboxes
# from a background thread. Kubernetes scales the API to several replicas and
# reaps via a CronJob (cleanup_once.py), so it must NOT also run a per-replica
# in-process thread.
if PLATFORM == "docker":
    orchestrator.start_cleanup_thread()
