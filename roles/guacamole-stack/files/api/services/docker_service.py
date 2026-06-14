import os
import threading
import time

import docker

from config import LAB_NETWORK, STUDENT_IMAGE
from services.db import create_db_user, delete_db_user
from services.guac import build_token, build_url
from state import active_sessions

docker_client = docker.from_env()


def start_cleanup_thread() -> None:
    threading.Thread(target=_cleanup_expired, daemon=True).start()


def _cleanup_expired() -> None:
    while True:
        time.sleep(60)
        now     = time.time()
        expired = [
            sid for sid, info in list(active_sessions.items())
            if now >= info["started_at"] + info["ttl_seconds"]
        ]
        for sid in expired:
            try:
                stop_container(sid)
                print(f"[cleanup] expired container stopped: {sid}")
            except Exception as exc:
                print(f"[cleanup] error stopping {sid}: {exc}")


def get_container_status(container_name: str) -> str:
    try:
        return docker_client.containers.get(container_name).status
    except Exception:
        return "not found"


def start_container(student_id: str, ttl: int, ttl_type: str) -> dict:
    try:
        existing = docker_client.containers.get(student_id)
        raise ValueError(
            f"Container for '{student_id}' already exists (status: {existing.status}). Stop it first."
        )
    except docker.errors.NotFound:
        pass

    docker_client.containers.run(
        STUDENT_IMAGE,
        name=student_id,
        hostname=student_id,
        network=LAB_NETWORK,
        detach=True,
        remove=False,
        mem_limit=os.getenv("STUDENT_MEMORY_LIMIT", "512m"),
        cpu_quota=int(os.getenv("STUDENT_CPU_QUOTA",  "50000")),
        cpu_period=int(os.getenv("STUDENT_CPU_PERIOD", "100000")),
        cap_drop=["ALL"],
        cap_add=["SETUID", "SETGID", "CHOWN", "SYS_CHROOT", "NET_BIND_SERVICE", "AUDIT_WRITE", "FOWNER"],
        security_opt=["no-new-privileges:true"],
        labels={"poc-role": "student", "student-id": student_id},
    )

    create_db_user(student_id)
    time.sleep(2)

    token = build_token(username=student_id, hostname=student_id, ttl=ttl)
    url   = build_url(token)

    active_sessions[student_id] = {
        "container":   student_id,
        "url":         url,
        "ttl_type":    ttl_type,
        "ttl_seconds": ttl,
        "started_at":  time.time(),
    }
    return {"student_id": student_id, **active_sessions[student_id]}


def stop_container(student_id: str) -> None:
    info           = active_sessions.get(student_id, {})
    container_name = info.get("container", student_id)
    try:
        c = docker_client.containers.get(container_name)
        c.stop(timeout=5)
        c.remove()
    except Exception:
        pass
    delete_db_user(student_id)
    active_sessions.pop(student_id, None)
