import time

from fastapi import APIRouter, HTTPException

from config import CLASS_TTL, HOMEWORK_TTL
from models import RedeployRequest, SessionRequest, StopRequest
from services.docker_service import get_container_status, start_container, stop_container
from state import active_sessions

router = APIRouter()


@router.get("/status")
def session_status():
    result = {}
    for student_id, info in list(active_sessions.items()):
        elapsed   = time.time() - info.get("started_at", time.time())
        remaining = max(0, info["ttl_seconds"] - int(elapsed))
        result[student_id] = {
            "status":        get_container_status(info["container"]),
            "url":           info["url"],
            "ttl_type":      info["ttl_type"],
            "ttl_remaining": remaining,
        }
    return result


@router.post("/start")
def start_session(req: SessionRequest):
    if req.ttl_type not in ("class", "homework"):
        raise HTTPException(status_code=400, detail="ttl_type must be 'class' or 'homework'")
    ttl             = CLASS_TTL if req.ttl_type == "class" else HOMEWORK_TTL
    results, errors = [], []
    for student_id in req.students:
        try:
            results.append(start_container(student_id, ttl, req.ttl_type))
        except ValueError as exc:
            errors.append({"student_id": student_id, "error": str(exc)})
        except Exception as exc:
            errors.append({"student_id": student_id, "error": str(exc)})
    return {"started": results, "errors": errors}


@router.delete("/stop")
def stop_session(req: StopRequest):
    stopped, errors = [], []
    for student_id in req.students:
        try:
            stop_container(student_id)
            stopped.append(student_id)
        except Exception as exc:
            errors.append({"student_id": student_id, "error": str(exc)})
    return {"stopped": stopped, "errors": errors}


@router.post("/redeploy")
def redeploy_session(req: RedeployRequest):
    if req.ttl_type not in ("class", "homework"):
        raise HTTPException(status_code=400, detail="ttl_type must be 'class' or 'homework'")
    ttl             = CLASS_TTL if req.ttl_type == "class" else HOMEWORK_TTL
    results, errors = [], []
    for student_id in req.students:
        try:
            stop_container(student_id)
            time.sleep(1)
            results.append(start_container(student_id, ttl, req.ttl_type))
        except Exception as exc:
            errors.append({"student_id": student_id, "error": str(exc)})
    return {"redeployed": results, "errors": errors}
