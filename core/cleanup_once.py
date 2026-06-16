"""Entrypoint for the Kubernetes cleanup CronJob: reap expired sandboxes once
and exit. The actual logic lives in the orchestrator so it stays identical to
the Docker in-process cleanup thread."""
from services.orchestrator import reap_expired_sessions

if __name__ == "__main__":
    reap_expired_sessions()
