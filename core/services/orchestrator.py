"""Sandbox lifecycle for both deployments, in one file.

A contributor changing how sandboxes are created should see both backends side
by side instead of hunting across two copies. The platform-specific surface is
deliberately tiny: each backend implements only `_provision`, `_deprovision` and
`get_container_status`. The shared `start_container`/`stop_container`/cleanup
logic below wraps them and is identical for Docker and Kubernetes.
"""
import time

from config import PLATFORM
from services import state_db
from services.db import create_db_user, delete_db_user
from services.guac import build_token, build_url

# ===========================================================================
# Kubernetes backend: sandboxes are Pods + Services, talked to via the K8s API.
# ===========================================================================
if PLATFORM == "kubernetes":
    import re

    from kubernetes import client, config as k8s_config
    from kubernetes.client.rest import ApiException

    from config import (
        STUDENT_CPU_LIMIT,
        STUDENT_CPU_REQUEST,
        STUDENT_IMAGE,
        STUDENT_MEMORY,
        STUDENT_NAMESPACE,
    )

    _STUDENT_ID_RE = re.compile(r"^[a-z]([-a-z0-9]{0,61}[a-z0-9])?$")
    _core_v1 = None

    def _client() -> "client.CoreV1Api":
        # Lazy: loading in-cluster config at import time would make this module
        # unimportable anywhere outside an actual Pod (tests, linters, a REPL).
        global _core_v1
        if _core_v1 is None:
            k8s_config.load_incluster_config()
            _core_v1 = client.CoreV1Api()
        return _core_v1

    def _pod_labels(student_id: str) -> dict:
        return {
            "app.kubernetes.io/part-of": "lab-platform",
            "lab-platform.io/role": "student",
            "lab-platform.io/student-id": student_id,
        }

    def get_container_status(student_id: str) -> str:
        try:
            pod = _client().read_namespaced_pod_status(student_id, STUDENT_NAMESPACE)
            return (pod.status.phase or "unknown").lower()
        except ApiException as exc:
            if exc.status == 404:
                return "not found"
            raise

    def _provision(student_id: str) -> str:
        if not _STUDENT_ID_RE.match(student_id):
            raise ValueError(
                f"'{student_id}' is not a valid sandbox name "
                "(lowercase letters, digits and '-' only, must start with a letter)"
            )

        try:
            _client().read_namespaced_pod(student_id, STUDENT_NAMESPACE)
            raise ValueError(f"Sandbox for '{student_id}' already exists. Stop it first.")
        except ApiException as exc:
            if exc.status != 404:
                raise

        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(name=student_id, labels=_pod_labels(student_id)),
            spec=client.V1PodSpec(
                restart_policy="Never",
                automount_service_account_token=False,
                containers=[
                    client.V1Container(
                        name="sandbox",
                        image=STUDENT_IMAGE,
                        ports=[client.V1ContainerPort(container_port=22)],
                        resources=client.V1ResourceRequirements(
                            requests={"cpu": STUDENT_CPU_REQUEST, "memory": STUDENT_MEMORY},
                            limits={"cpu": STUDENT_CPU_LIMIT, "memory": STUDENT_MEMORY},
                        ),
                        security_context=client.V1SecurityContext(
                            allow_privilege_escalation=False,
                            capabilities=client.V1Capabilities(
                                drop=["ALL"],
                                add=["SETUID", "SETGID", "CHOWN", "NET_BIND_SERVICE"],
                            ),
                            seccomp_profile=client.V1SeccompProfile(type="RuntimeDefault"),
                        ),
                    )
                ],
            ),
        )
        _client().create_namespaced_pod(STUDENT_NAMESPACE, pod)

        service = client.V1Service(
            metadata=client.V1ObjectMeta(name=student_id, labels=_pod_labels(student_id)),
            spec=client.V1ServiceSpec(
                selector={"lab-platform.io/student-id": student_id},
                ports=[client.V1ServicePort(port=22, target_port=22)],
            ),
        )
        _client().create_namespaced_service(STUDENT_NAMESPACE, service)

        return f"{student_id}.{STUDENT_NAMESPACE}.svc.cluster.local"

    def _deprovision(student_id: str, wait_seconds: float = 10) -> None:
        core = _client()
        for delete in (core.delete_namespaced_pod, core.delete_namespaced_service):
            try:
                delete(student_id, STUDENT_NAMESPACE, grace_period_seconds=0)
            except ApiException as exc:
                if exc.status != 404:
                    raise

        # Wait for the Pod to actually disappear so an immediate redeploy doesn't
        # collide with the old object name while it's still terminating.
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            try:
                core.read_namespaced_pod(student_id, STUDENT_NAMESPACE)
            except ApiException as exc:
                if exc.status == 404:
                    break
                raise
            time.sleep(0.5)

# ===========================================================================
# Docker Compose backend: sandboxes are containers on a shared bridge network.
# ===========================================================================
else:
    import docker

    from config import LAB_NETWORK, STUDENT_CPU_PERIOD, STUDENT_CPU_QUOTA, STUDENT_IMAGE, STUDENT_MEMORY_LIMIT

    _docker = docker.from_env()

    def get_container_status(container_name: str) -> str:
        try:
            return _docker.containers.get(container_name).status
        except Exception:
            return "not found"

    def _provision(student_id: str) -> str:
        try:
            existing = _docker.containers.get(student_id)
            raise ValueError(
                f"Container for '{student_id}' already exists "
                f"(status: {existing.status}). Stop it first."
            )
        except docker.errors.NotFound:
            pass

        _docker.containers.run(
            STUDENT_IMAGE,
            name=student_id,
            hostname=student_id,
            network=LAB_NETWORK,
            detach=True,
            remove=False,
            mem_limit=STUDENT_MEMORY_LIMIT,
            cpu_quota=STUDENT_CPU_QUOTA,
            cpu_period=STUDENT_CPU_PERIOD,
            cap_drop=["ALL"],
            cap_add=["SETUID", "SETGID", "CHOWN", "SYS_CHROOT", "NET_BIND_SERVICE", "AUDIT_WRITE", "FOWNER"],
            security_opt=["no-new-privileges:true"],
            labels={"poc-role": "student", "student-id": student_id},
        )
        time.sleep(2)  # let the container's SSH daemon come up before issuing the token
        return student_id  # reachable by container name on the lab network

    def _deprovision(student_id: str) -> None:
        try:
            c = _docker.containers.get(student_id)
            c.stop(timeout=5)
            c.remove()
        except Exception:
            pass


# ===========================================================================
# Shared lifecycle: identical for both backends, built on the seam above.
# ===========================================================================
def start_container(student_id: str, ttl: int, ttl_type: str) -> dict:
    hostname   = _provision(student_id)
    create_db_user(student_id)
    token      = build_token(username=student_id, hostname=hostname, ttl=ttl)
    url        = build_url(token)
    started_at = time.time()

    state_db.upsert_session(student_id, student_id, url, ttl_type, ttl, started_at)
    return {
        "student_id":  student_id,
        "container":   student_id,
        "url":         url,
        "ttl_type":    ttl_type,
        "ttl_seconds": ttl,
        "started_at":  started_at,
    }


def stop_container(student_id: str) -> None:
    _deprovision(student_id)
    delete_db_user(student_id)
    state_db.delete_session(student_id)


def reap_expired_sessions() -> None:
    """Stop sandboxes whose TTL has passed. Used by both the Docker in-process
    cleanup thread and the Kubernetes cleanup CronJob (cleanup_once.py)."""
    now = time.time()
    sessions = state_db.list_all_sessions()

    for session in sessions:
        student_id = session["student_id"]
        if now >= session["started_at"] + session["ttl_seconds"]:
            try:
                stop_container(student_id)
                print(f"[cleanup] expired session stopped: {student_id}")
            except Exception as exc:
                print(f"[cleanup] error stopping {student_id}: {exc}")

    # On Kubernetes the session row and the Pod are separate objects, so a Pod
    # can vanish (node drain, OOM) while its row lingers; reconcile those away.
    # The Docker store is in-process, so this can't happen there.
    if PLATFORM == "kubernetes":
        for session in sessions:
            student_id = session["student_id"]
            still_live = now < session["started_at"] + session["ttl_seconds"]
            if still_live and get_container_status(student_id) == "not found":
                state_db.delete_session(student_id)
                print(f"[cleanup] orphaned session row removed (pod missing): {student_id}")


def start_cleanup_thread() -> None:
    """Docker Compose only: one API container reaps its own expired sandboxes
    from a background thread (Kubernetes uses the cleanup CronJob instead)."""
    import threading

    def _loop() -> None:
        while True:
            time.sleep(60)
            reap_expired_sessions()

    threading.Thread(target=_loop, daemon=True).start()
