from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import docker
import psycopg2
import json
import hmac
import hashlib
import os
import base64
import time
import uuid
import urllib.parse
import csv
import io
import threading
 
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
 
app = FastAPI(
    title="Lab Provisioning API",
    description="Start/stop student container sessions and generate Guacamole token links."
)
 
# ── env ────────────────────────────────────────────────────────────────────────
JSON_SECRET_KEY  = os.getenv("JSON_SECRET_KEY",  "00000000000000000000000000000000")
STUDENT_IMAGE    = os.getenv("STUDENT_IMAGE",    "student-image:latest")
LAB_NETWORK      = os.getenv("LAB_NETWORK",      "lab-students")
PUBLIC_URL       = os.getenv("PUBLIC_URL",        "https://localhost/guacamole")
POSTGRES_DSN     = os.getenv("POSTGRES_DSN",      "")
CLASS_TTL        = int(os.getenv("CLASS_TTL_SECONDS",    "7200"))
HOMEWORK_TTL     = int(os.getenv("HOMEWORK_TTL_SECONDS", "604800"))
SSH_PORT         = 22
SSH_USER         = "labuser"
SSH_PASS         = "labpass"
CONNECTION_NAME  = "terminal"
 
docker_client = docker.from_env()
 
active_sessions: dict[str, dict] = {}
 
# ── roster (hardcoded defaults — stays as-is, mutable for runtime additions) ──
CLASSES: dict[str, list[str]] = {
    "Class A": [
        "alice", "bob", "charlie", "diana", "eve",
        "frank", "grace", "hank", "iris", "jack",
        "karen", "leo", "mia", "noah", "olivia",
        "paul", "quinn", "rachel", "sam", "tina",
        "uma", "victor", "wendy", "xander", "yara",
    ],
    "Class B": [
        "aaron", "bella", "carl", "dora", "eli",
        "fiona", "george", "helen", "ivan", "julia",
        "kevin", "luna", "mike", "nora", "oscar",
        "petra", "quentin", "rosa", "steve", "tara",
        "ulrich", "vera", "walter", "xenia", "zoe",
    ],
}
 
 
# ── cleanup thread ─────────────────────────────────────────────────────────────
def _cleanup_expired():
    while True:
        time.sleep(60)
        now = time.time()
        expired = [
            sid for sid, info in list(active_sessions.items())
            if now >= info["started_at"] + info["ttl_seconds"]
        ]
        for sid in expired:
            try:
                _stop_one(sid)
                print(f"[cleanup] Expired container stopped: {sid}")
            except Exception as e:
                print(f"[cleanup] Error stopping {sid}: {e}")
 
threading.Thread(target=_cleanup_expired, daemon=True).start()
 
 
# ── helpers ────────────────────────────────────────────────────────────────────
def _db_conn():
    return psycopg2.connect(POSTGRES_DSN)
 
 
def _guac_token(username: str, hostname: str, ttl: int) -> str:
    key = bytes.fromhex(JSON_SECRET_KEY)
    payload = {
        "username": username,
        "expires":  int((time.time() + ttl) * 1000),
        "connections": {
            CONNECTION_NAME: {
                "protocol": "ssh",
                "parameters": {
                    "hostname": hostname,
                    "port":     str(SSH_PORT),
                    "username": SSH_USER,
                    "password": SSH_PASS,
                }
            }
        }
    }
    json_bytes = json.dumps(payload).encode("utf-8")
    signature  = hmac.new(key, json_bytes, hashlib.sha256).digest()
    plaintext  = signature + json_bytes
    cipher     = AES.new(key, AES.MODE_CBC, bytes(16))
    ciphertext = cipher.encrypt(pad(plaintext, 16))
    return base64.b64encode(ciphertext).decode()
 
 
def _guac_url(token: str) -> str:
    return f"{PUBLIC_URL}/?data={urllib.parse.quote(token, safe='')}"
 
 
def _create_db_user(student_id: str) -> None:
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO guacamole_entity (name, type) VALUES (%s, 'USER') ON CONFLICT DO NOTHING",
                (student_id,)
            )
            cur.execute(
                """
                INSERT INTO guacamole_user (entity_id, password_hash, password_salt, password_date)
                SELECT entity_id, '\\\\x00'::bytea, '\\\\x00'::bytea, NOW()
                FROM guacamole_entity WHERE name = %s AND type = 'USER'
                ON CONFLICT DO NOTHING
                """,
                (student_id,)
            )
            conn.commit()
 
 
def _delete_db_user(student_id: str) -> None:
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM guacamole_entity WHERE name = %s AND type = 'USER'",
                (student_id,)
            )
            conn.commit()
 
 
def _start_one(student_id: str, ttl: int, ttl_type: str) -> dict:
    try:
        existing = docker_client.containers.get(student_id)
        raise ValueError(f"Container for '{student_id}' already exists (status: {existing.status}). Stop it first.")
    except docker.errors.NotFound:
        pass
 
    container = docker_client.containers.run(
        STUDENT_IMAGE,
        name=student_id,
        hostname=student_id,
        network=LAB_NETWORK,
        detach=True,
        remove=False,
        mem_limit=os.getenv("STUDENT_MEMORY_LIMIT", "512m"),
        cpu_quota=int(os.getenv("STUDENT_CPU_QUOTA", "50000")),
        cpu_period=int(os.getenv("STUDENT_CPU_PERIOD", "100000")),
        cap_drop=["ALL"],
        cap_add=["SETUID", "SETGID", "CHOWN", "SYS_CHROOT", "NET_BIND_SERVICE", "AUDIT_WRITE", "FOWNER"],
        security_opt=["no-new-privileges:true"],
        labels={"poc-role": "student", "student-id": student_id},
    )
    _create_db_user(student_id)
    time.sleep(2)
    token = _guac_token(username=student_id, hostname=student_id, ttl=ttl)
    url   = _guac_url(token)
    active_sessions[student_id] = {
        "container":   student_id,
        "url":         url,
        "ttl_type":    ttl_type,
        "ttl_seconds": ttl,
        "started_at":  time.time(),
    }
    return {"student_id": student_id, **active_sessions[student_id]}
 
 
def _stop_one(student_id: str) -> None:
    info           = active_sessions.get(student_id, {})
    container_name = info.get("container", student_id)
    try:
        c = docker_client.containers.get(container_name)
        c.stop(timeout=5)
        c.remove()
    except Exception:
        pass
    _delete_db_user(student_id)
    active_sessions.pop(student_id, None)
 
 
# ── models ─────────────────────────────────────────────────────────────────────
class SessionRequest(BaseModel):
    students: list[str]
    ttl_type: str = "class"
 
class StopRequest(BaseModel):
    students: list[str]
 
class RedeployRequest(BaseModel):
    students: list[str]
    ttl_type: str = "class"
 
class AddClassRequest(BaseModel):
    class_name: str
 
class AddStudentRequest(BaseModel):
    student_id: str
 
 
# ── roster routes ──────────────────────────────────────────────────────────────
@app.get("/roster")
def get_roster():
    return CLASSES
 
 
@app.post("/roster/class")
def add_class(req: AddClassRequest):
    name = req.class_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="class_name cannot be empty")
    if name in CLASSES:
        raise HTTPException(status_code=409, detail=f"Class '{name}' already exists")
    CLASSES[name] = []
    return {"created": name, "roster": CLASSES}
 
 
@app.delete("/roster/class/{class_name}")
def remove_class(class_name: str):
    if class_name not in CLASSES:
        raise HTTPException(status_code=404, detail=f"Class '{class_name}' not found")
    del CLASSES[class_name]
    return {"deleted": class_name, "roster": CLASSES}
 
 
@app.post("/roster/class/{class_name}/student")
def add_student(class_name: str, req: AddStudentRequest):
    if class_name not in CLASSES:
        raise HTTPException(status_code=404, detail=f"Class '{class_name}' not found")
    sid = req.student_id.strip().lower()
    if not sid:
        raise HTTPException(status_code=400, detail="student_id cannot be empty")
    if sid in CLASSES[class_name]:
        raise HTTPException(status_code=409, detail=f"'{sid}' is already in '{class_name}'")
    CLASSES[class_name].append(sid)
    return {"added": sid, "class": class_name, "total": len(CLASSES[class_name])}
 
 
@app.delete("/roster/class/{class_name}/student/{student_id}")
def remove_student(class_name: str, student_id: str):
    if class_name not in CLASSES:
        raise HTTPException(status_code=404, detail=f"Class '{class_name}' not found")
    if student_id not in CLASSES[class_name]:
        raise HTTPException(status_code=404, detail=f"'{student_id}' not found in '{class_name}'")
    CLASSES[class_name].remove(student_id)
    return {"removed": student_id, "class": class_name, "total": len(CLASSES[class_name])}
 
 
@app.post("/roster/import")
async def import_roster_csv(file: UploadFile = File(...)):
    """
    Import classes and students from a CSV file.
 
    Expected format (with or without header row):
        class_name,student_id
        Class A,alice
        Class B,bob
 
    - Creates the class if it doesn't exist yet.
    - Skips duplicate students silently.
    - Returns a summary of what was added and any row-level errors.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")
 
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")   # strip BOM if present
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")
 
    reader = csv.reader(io.StringIO(text))
    added: dict[str, list[str]] = {}
    skipped: list[str] = []
    errors: list[str] = []
 
    for i, row in enumerate(reader):
        # skip blank rows
        if not any(cell.strip() for cell in row):
            continue
        # skip header row if present
        if i == 0 and row[0].strip().lower() in ("class", "class_name", "classname"):
            continue
        if len(row) < 2:
            errors.append(f"Row {i+1}: expected 2 columns (class_name, student_id), got {len(row)}")
            continue
 
        class_name = row[0].strip()
        student_id = row[1].strip().lower()
 
        if not class_name:
            errors.append(f"Row {i+1}: class_name is empty")
            continue
        if not student_id:
            errors.append(f"Row {i+1}: student_id is empty")
            continue
 
        # auto-create class
        if class_name not in CLASSES:
            CLASSES[class_name] = []
 
        if student_id in CLASSES[class_name]:
            skipped.append(f"{class_name}/{student_id}")
        else:
            CLASSES[class_name].append(student_id)
            added.setdefault(class_name, []).append(student_id)
 
    total_added = sum(len(v) for v in added.values())
    return {
        "added":   added,
        "total_added": total_added,
        "skipped": skipped,
        "errors":  errors,
        "roster":  CLASSES,
    }
 
 
@app.get("/roster/export")
def export_roster_csv():
    """Download the current full roster as a CSV file."""
    from fastapi.responses import StreamingResponse
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["class_name", "student_id"])
    for class_name, students in CLASSES.items():
        for s in students:
            writer.writerow([class_name, s])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=roster.csv"},
    )
 
 
# ── session routes ─────────────────────────────────────────────────────────────
@app.get("/session/status")
def session_status():
    result = {}
    for student_id, info in list(active_sessions.items()):
        try:
            c      = docker_client.containers.get(info["container"])
            status = c.status
        except Exception:
            status = "not found"
        elapsed   = time.time() - info.get("started_at", time.time())
        remaining = max(0, info["ttl_seconds"] - int(elapsed))
        result[student_id] = {
            "status":        status,
            "url":           info["url"],
            "ttl_type":      info["ttl_type"],
            "ttl_remaining": remaining,
        }
    return result
 
 
@app.post("/session/start")
def start_session(req: SessionRequest):
    if req.ttl_type not in ("class", "homework"):
        raise HTTPException(status_code=400, detail="ttl_type must be 'class' or 'homework'")
    ttl = CLASS_TTL if req.ttl_type == "class" else HOMEWORK_TTL
    results, errors = [], []
    for student_id in req.students:
        try:
            results.append(_start_one(student_id, ttl, req.ttl_type))
        except ValueError as exc:
            errors.append({"student_id": student_id, "error": str(exc)})
        except Exception as exc:
            errors.append({"student_id": student_id, "error": str(exc)})
    return {"started": results, "errors": errors}
 
 
@app.delete("/session/stop")
def stop_session(req: StopRequest):
    stopped, errors = [], []
    for student_id in req.students:
        try:
            _stop_one(student_id)
            stopped.append(student_id)
        except Exception as exc:
            errors.append({"student_id": student_id, "error": str(exc)})
    return {"stopped": stopped, "errors": errors}
 
 
@app.post("/session/redeploy")
def redeploy_session(req: RedeployRequest):
    if req.ttl_type not in ("class", "homework"):
        raise HTTPException(status_code=400, detail="ttl_type must be 'class' or 'homework'")
    ttl = CLASS_TTL if req.ttl_type == "class" else HOMEWORK_TTL
    results, errors = [], []
    for student_id in req.students:
        try:
            _stop_one(student_id)
            time.sleep(1)
            results.append(_start_one(student_id, ttl, req.ttl_type))
        except Exception as exc:
            errors.append({"student_id": student_id, "error": str(exc)})
    return {"redeployed": results, "errors": errors}
 
 
# ── dashboard ──────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return HTMLResponse("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lab Dashboard</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: system-ui, sans-serif; background: #0f1117; color: #e2e8f0; min-height: 100vh; }
 
/* ── topbar ── */
.topbar { background: #1e2130; border-bottom: 1px solid #2d3148; padding: .75rem 2rem; display: flex; align-items: center; justify-content: space-between; gap: 1rem; flex-wrap: wrap; }
.topbar h1 { font-size: 1.1rem; color: #fff; font-weight: 600; white-space: nowrap; }
.tabs { display: flex; gap: .25rem; flex-wrap: wrap; align-items: center; }
.tab { padding: .4rem 1rem; border-radius: 6px; cursor: pointer; font-size: .875rem; color: #94a3b8; border: 1px solid transparent; }
.tab.active { background: #2d3148; color: #fff; border-color: #3d4468; }
.tab:hover:not(.active) { background: #252840; color: #cbd5e1; }
.tab-add { padding: .4rem .7rem; border-radius: 6px; cursor: pointer; font-size: .875rem; color: #64748b; border: 1px dashed #2d3148; }
.tab-add:hover { color: #94a3b8; border-color: #3d4468; }
 
/* ── main ── */
.main { padding: 1.5rem 2rem; }
.toolbar { display: flex; gap: .75rem; align-items: center; flex-wrap: wrap; margin-bottom: 1rem; }
select, button, input[type=text] { font-size: .875rem; border-radius: 6px; padding: .4rem .9rem; cursor: pointer; border: 1px solid #2d3148; }
select, input[type=text] { background: #1e2130; color: #e2e8f0; }
input[type=text] { cursor: text; }
input[type=text]::placeholder { color: #4b5563; }
button { background: #1e2130; color: #e2e8f0; }
button.primary { background: #3b82f6; border-color: #3b82f6; color: #fff; font-weight: 600; }
button.danger  { background: #ef4444; border-color: #ef4444; color: #fff; }
button.warning { background: #f59e0b; border-color: #f59e0b; color: #000; font-weight: 600; }
button.success { background: #22c55e; border-color: #22c55e; color: #000; font-weight: 600; }
button:hover { opacity: .85; }
button:disabled { opacity: .4; cursor: not-allowed; }
.sep { width: 1px; height: 24px; background: #2d3148; flex-shrink: 0; }
 
/* ── search ── */
.search-row { display: flex; gap: .75rem; margin-bottom: 1rem; align-items: center; }
#searchInput { flex: 0 0 220px; padding: .38rem .8rem; }
.student-count-label { font-size: .78rem; color: #64748b; }
 
/* ── student grid ── */
.student-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: .6rem; margin-bottom: 1.5rem; }
.student-card { background: #1e2130; border: 1px solid #2d3148; border-radius: 8px; padding: .65rem .75rem; display: flex; align-items: center; gap: .5rem; cursor: pointer; transition: border-color .15s; user-select: none; position: relative; }
.student-card:hover { border-color: #3b82f6; }
.student-card.selected { border-color: #3b82f6; background: #1a2540; }
.student-card.running { border-color: #22c55e44; }
.student-card.selected.running { border-color: #22c55e; background: #142418; }
.student-card input[type=checkbox] { accent-color: #3b82f6; width: 15px; height: 15px; flex-shrink: 0; pointer-events: none; }
.student-name { font-size: .875rem; font-weight: 500; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #334155; flex-shrink: 0; }
.dot.running { background: #22c55e; }
.remove-student-btn { position: absolute; top: 4px; right: 5px; background: none; border: none; color: #4b5563; font-size: .7rem; padding: 2px 4px; border-radius: 3px; cursor: pointer; opacity: 0; transition: opacity .15s, color .15s; line-height: 1; }
.student-card:hover .remove-student-btn { opacity: 1; }
.remove-student-btn:hover { color: #ef4444 !important; background: #2d1b1b; opacity: 1 !important; }
 
/* ── manage panel ── */
.manage-toggle { display: flex; align-items: center; gap: .5rem; cursor: pointer; font-size: .82rem; color: #64748b; margin-bottom: .75rem; user-select: none; }
.manage-toggle:hover { color: #94a3b8; }
.manage-toggle .arrow { display: inline-block; transition: transform .2s; font-size: .7rem; }
.manage-toggle.open .arrow { transform: rotate(90deg); }
.manage-panel { background: #161926; border: 1px solid #2d3148; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1.25rem; display: none; }
.manage-panel.open { display: block; }
.manage-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(250px, 1fr)); gap: 1.25rem; }
.manage-section h3 { font-size: .75rem; text-transform: uppercase; letter-spacing: .07em; color: #64748b; margin-bottom: .6rem; }
.manage-section .row { display: flex; gap: .5rem; align-items: center; flex-wrap: wrap; }
.manage-section input[type=text] { flex: 1; min-width: 120px; }
.csv-drop { border: 1px dashed #334155; border-radius: 6px; padding: .75rem; text-align: center; font-size: .8rem; color: #64748b; cursor: pointer; transition: border-color .15s, background .15s; }
.csv-drop:hover, .csv-drop.dragover { border-color: #3b82f6; background: #1a2540; color: #93c5fd; }
.csv-drop input[type=file] { display: none; }
.manage-msg { font-size: .78rem; margin-top: .5rem; min-height: 1.1em; }
.manage-msg.ok  { color: #4ade80; }
.manage-msg.err { color: #f87171; }
 
/* ── section title ── */
.section-title { font-size: .75rem; text-transform: uppercase; letter-spacing: .06em; color: #64748b; margin-bottom: .75rem; }
 
/* ── active table ── */
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: .72rem; color: #64748b; text-transform: uppercase; letter-spacing: .05em; padding: .5rem .75rem; border-bottom: 1px solid #2d3148; }
td { padding: .55rem .75rem; border-bottom: 1px solid #1a1f30; font-size: .85rem; vertical-align: middle; }
tr:hover td { background: #252840; }
.badge { display: inline-block; padding: .15rem .5rem; border-radius: 4px; font-size: .72rem; font-weight: 600; }
.badge.class    { background: #1d4ed8; color: #bfdbfe; }
.badge.homework { background: #7c3aed; color: #ddd6fe; }
.copy-btn { background: #334155; border: none; color: #94a3b8; border-radius: 4px; padding: .25rem .6rem; font-size: .78rem; cursor: pointer; }
.copy-btn:hover { background: #475569; color: #fff; }
.copy-btn.copied { background: #166534; color: #86efac; }
.act-btn { background: #1e2130; border: 1px solid #2d3148; color: #94a3b8; border-radius: 4px; padding: .25rem .6rem; font-size: .78rem; cursor: pointer; margin-left: .3rem; }
.act-btn:hover { background: #252840; color: #fff; }
.act-btn.red { border-color: #ef444440; color: #f87171; }
.act-btn.red:hover { background: #7f1d1d; color: #fca5a5; }
.empty-class { text-align: center; padding: 2rem; color: #4b5563; font-size: .875rem; }
 
/* ── status / toast ── */
#statusbar { font-size: .82rem; color: #64748b; margin-bottom: 1rem; min-height: 1.2em; }
#errorBox { background: #2d1b1b; border: 1px solid #7f1d1d; border-radius: 6px; padding: .75rem 1rem; margin-bottom: 1rem; font-size: .82rem; color: #fca5a5; display: none; }
#toast { position: fixed; bottom: 1.5rem; right: 1.5rem; background: #1e2130; border: 1px solid #2d3148; border-radius: 8px; padding: .6rem 1.2rem; font-size: .85rem; opacity: 0; transform: translateY(8px); transition: all .2s; pointer-events: none; z-index: 999; max-width: 320px; }
#toast.show { opacity: 1; transform: translateY(0); }
 
/* ── modal ── */
.modal-bg { position: fixed; inset: 0; background: #00000088; z-index: 100; display: flex; align-items: center; justify-content: center; }
.modal { background: #1e2130; border: 1px solid #3d4468; border-radius: 10px; padding: 1.5rem; min-width: 320px; max-width: 420px; width: 90%; }
.modal h2 { font-size: 1rem; margin-bottom: 1rem; }
.modal label { font-size: .8rem; color: #94a3b8; display: block; margin-bottom: .25rem; margin-top: .75rem; }
.modal input[type=text] { width: 100%; }
.modal-actions { display: flex; gap: .5rem; justify-content: flex-end; margin-top: 1.25rem; }
</style>
</head>
<body>
 
<div class="topbar">
  <h1>🖥 Lab Dashboard</h1>
  <div class="tabs" id="classTabs">
    <!-- rendered by JS -->
  </div>
</div>
 
<div class="main">
  <div id="statusbar">Loading…</div>
  <div id="errorBox"></div>
 
  <!-- ── manage roster panel ── -->
  <div class="manage-toggle" id="manageToggle" onclick="toggleManage()">
    <span class="arrow">▶</span> Manage Roster
  </div>
  <div class="manage-panel" id="managePanel">
    <div class="manage-grid">
 
      <!-- Import CSV -->
      <div class="manage-section">
        <h3>📂 Import via CSV</h3>
        <div class="csv-drop" id="csvDrop" onclick="document.getElementById('csvFile').click()"
             ondragover="event.preventDefault();this.classList.add('dragover')"
             ondragleave="this.classList.remove('dragover')"
             ondrop="handleCsvDrop(event)">
          <input type="file" id="csvFile" accept=".csv" onchange="uploadCsv(this.files[0])">
          Click or drag a <b>.csv</b> file here<br>
          <span style="font-size:.72rem;color:#475569">Format: <code>class_name,student_id</code> (one per row)</span>
        </div>
        <div class="manage-msg" id="csvMsg"></div>
        <div style="margin-top:.5rem">
          <a href="/api/roster/export" style="font-size:.78rem;color:#64748b;text-decoration:none;">⬇ Export current roster as CSV</a>
        </div>
      </div>
 
      <!-- Add Class -->
      <div class="manage-section">
        <h3>➕ Add New Class</h3>
        <div class="row">
          <input type="text" id="newClassName" placeholder="e.g. Class C" onkeydown="if(event.key==='Enter')addClassUI()">
          <button class="primary" onclick="addClassUI()">Add class</button>
        </div>
        <div class="manage-msg" id="classMsg"></div>
 
        <h3 style="margin-top:1rem">🗑 Remove Current Class</h3>
        <div class="row">
          <span style="font-size:.82rem;color:#94a3b8" id="removeClassLabel">—</span>
          <button class="danger" onclick="removeClassUI()" id="removeClassBtn" style="padding:.35rem .75rem">Remove</button>
        </div>
        <div class="manage-msg" id="removeClassMsg"></div>
      </div>
 
      <!-- Add / Remove Student -->
      <div class="manage-section">
        <h3>👤 Add Student to <span id="addStudentClassLabel">…</span></h3>
        <div class="row">
          <input type="text" id="newStudentId" placeholder="student username" onkeydown="if(event.key==='Enter')addStudentUI()">
          <button class="primary" onclick="addStudentUI()">Add</button>
        </div>
        <div class="manage-msg" id="studentMsg"></div>
        <p style="font-size:.75rem;color:#475569;margin-top:.4rem">
          Tip: click the <b>×</b> on any card to remove that student from this class.
        </p>
      </div>
 
    </div>
  </div>
 
  <!-- ── toolbar ── -->
  <div class="toolbar">
    <button onclick="selectAll()">☑ Select all</button>
    <button onclick="selectNone()">☐ Deselect all</button>
    <div class="sep"></div>
    <select id="ttlType">
      <option value="class">Class (2 h)</option>
      <option value="homework">Homework (7 d)</option>
    </select>
    <button class="primary" onclick="startSelected()">▶ Start selected</button>
    <div class="sep"></div>
    <button class="warning" onclick="redeploySelected()">↺ Redeploy selected</button>
    <button class="danger"  onclick="stopSelected()">■ Stop selected</button>
    <button class="danger"  onclick="stopAll()">■ Stop all</button>
  </div>
 
  <!-- ── search + count ── -->
  <div class="search-row">
    <input type="text" id="searchInput" placeholder="🔍  Filter students…" oninput="renderGrid()">
    <span class="student-count-label" id="selectedCount">0 selected</span>
  </div>
 
  <div class="section-title">Students</div>
  <div class="student-grid" id="studentGrid"></div>
 
  <div id="activeSection" style="display:none">
    <div class="section-title" style="margin-top:.5rem">Active containers</div>
    <table>
      <thead><tr><th>Student</th><th>Type</th><th>Time remaining</th><th>Link</th><th>Actions</th></tr></thead>
      <tbody id="activeBody"></tbody>
    </table>
  </div>
</div>
 
<div id="toast"></div>
 
<script>
let roster       = {};
let currentClass = null;
let sessionLinks = {};
let sessionMeta  = {};
let runningSet   = new Set();

// ── init ──────────────────────────────────────────────────────────────────────
async function init() {
  await loadRoster();
  await syncStatus();
  setInterval(syncStatus, 15000);
}

async function loadRoster() {
  const res = await fetch('/api/roster');
  roster = await res.json();
  const classes = Object.keys(roster);
  if (!currentClass || !roster[currentClass]) {
    currentClass = classes[0] || null;
  }
  renderTabs();
  renderGrid();
}

// ── tabs ──────────────────────────────────────────────────────────────────────
function renderTabs() {
  const container = document.getElementById('classTabs');
  container.innerHTML = '';
  Object.keys(roster).forEach(cls => {
    const div = document.createElement('div');
    div.className = 'tab' + (cls === currentClass ? ' active' : '');
    div.textContent = cls;
    div.onclick = () => switchClass(cls);
    container.appendChild(div);
  });
  // update manage panel labels
  const lbl = document.getElementById('removeClassLabel');
  const addLbl = document.getElementById('addStudentClassLabel');
  if (lbl) lbl.textContent = currentClass || '—';
  if (addLbl) addLbl.textContent = currentClass || '—';
}

function switchClass(cls) {
  currentClass = cls;
  document.getElementById('searchInput').value = '';
  renderTabs();
  renderGrid();
}

// ── manage panel ──────────────────────────────────────────────────────────────
function toggleManage() {
  const toggle = document.getElementById('manageToggle');
  const panel  = document.getElementById('managePanel');
  toggle.classList.toggle('open');
  panel.classList.toggle('open');
}

async function addClassUI() {
  const input = document.getElementById('newClassName');
  const name  = input.value.trim();
  const msg   = document.getElementById('classMsg');
  if (!name) { setManageMsg(msg, 'Enter a class name.', true); return; }
  const res  = await fetch('/api/roster/class', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ class_name: name })
  });
  const data = await res.json();
  if (!res.ok) { setManageMsg(msg, data.detail || 'Error', true); return; }
  input.value = '';
  setManageMsg(msg, `✓ Class "${name}" created.`, false);
  await loadRoster();
  switchClass(name);
}

async function removeClassUI() {
  if (!currentClass) return;
  const msg = document.getElementById('removeClassMsg');
  if (!confirm(`Remove class "${currentClass}" and all its students from the roster?\n(Running containers are NOT stopped.)`)) return;
  const res  = await fetch(`/api/roster/class/${encodeURIComponent(currentClass)}`, { method: 'DELETE' });
  const data = await res.json();
  if (!res.ok) { setManageMsg(msg, data.detail || 'Error', true); return; }
  setManageMsg(msg, `✓ Removed "${currentClass}".`, false);
  currentClass = null;
  await loadRoster();
}

async function addStudentUI() {
  if (!currentClass) return;
  const input = document.getElementById('newStudentId');
  const sid   = input.value.trim();
  const msg   = document.getElementById('studentMsg');
  if (!sid) { setManageMsg(msg, 'Enter a student username.', true); return; }
  const res  = await fetch(`/api/roster/class/${encodeURIComponent(currentClass)}/student`, {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ student_id: sid })
  });
  const data = await res.json();
  if (!res.ok) { setManageMsg(msg, data.detail || 'Error', true); return; }
  input.value = '';
  setManageMsg(msg, `✓ Added "${sid}" to ${currentClass}.`, false);
  await loadRoster();
  renderGrid();
}

async function removeStudentUI(cls, studentId, event) {
  event.stopPropagation();
  if (!confirm(`Remove "${studentId}" from ${cls}?\n(Running container is NOT stopped.)`)) return;
  const res  = await fetch(`/api/roster/class/${encodeURIComponent(cls)}/student/${encodeURIComponent(studentId)}`, { method: 'DELETE' });
  const data = await res.json();
  if (!res.ok) { showToast(`⚠ ${data.detail || 'Error removing student'}`); return; }
  showToast(`✓ Removed "${studentId}" from ${cls}`);
  await loadRoster();
  renderGrid();
}

function handleCsvDrop(event) {
  event.preventDefault();
  document.getElementById('csvDrop').classList.remove('dragover');
  const file = event.dataTransfer.files[0];
  if (file) uploadCsv(file);
}

async function uploadCsv(file) {
  if (!file) return;
  const msg = document.getElementById('csvMsg');
  msg.textContent = '⏳ Uploading…';
  msg.className   = 'manage-msg';
  const form = new FormData();
  form.append('file', file);
  const res  = await fetch('/api/roster/import', { method: 'POST', body: form });
  const data = await res.json();
  if (!res.ok) {
    setManageMsg(msg, data.detail || 'Upload failed', true);
    return;
  }
  const lines = [`✓ Added ${data.total_added} student(s).`];
  if (data.skipped.length)  lines.push(`Skipped ${data.skipped.length} duplicate(s).`);
  if (data.errors.length)   lines.push(`${data.errors.length} row error(s): ${data.errors.slice(0,3).join('; ')}`);
  setManageMsg(msg, lines.join(' '), data.errors.length > 0);
  document.getElementById('csvFile').value = '';
  await loadRoster();
}

function setManageMsg(el, text, isErr) {
  el.textContent = text;
  el.className   = 'manage-msg ' + (isErr ? 'err' : 'ok');
  setTimeout(() => { el.textContent = ''; el.className = 'manage-msg'; }, 5000);
}

// ── sync ──────────────────────────────────────────────────────────────────────
async function syncStatus() {
  try {
    const res  = await fetch('/api/session/status');
    const data = await res.json();
    runningSet = new Set(Object.keys(data).filter(k => data[k].status === 'running'));
    Object.entries(data).forEach(([id, info]) => {
      if (info.url)                        sessionLinks[id] = info.url;
      if (info.ttl_remaining !== undefined) sessionMeta[id]  = info;
    });
    setStatus(`Last synced: ${new Date().toLocaleTimeString()} — ${runningSet.size} container(s) running`);
    renderGrid();
  } catch(e) {
    setStatus('⚠ Could not reach API');
  }
}

// ── grid ──────────────────────────────────────────────────────────────────────
function renderGrid() {
  const grid     = document.getElementById('studentGrid');
  const filter   = (document.getElementById('searchInput').value || '').toLowerCase();
  const students = (roster[currentClass] || []).filter(s => !filter || s.includes(filter));
  const selected = getSelected();
  grid.innerHTML  = '';

  if (!students.length) {
    const empty = document.createElement('div');
    empty.className = 'empty-class';
    empty.textContent = filter ? 'No students match the filter.' : 'This class has no students yet.';
    grid.appendChild(empty);
    updateCount();
    renderActiveTable();
    return;
  }

  students.forEach(name => {
    const running = runningSet.has(name);
    const card    = document.createElement('div');
    card.className = 'student-card' + (running ? ' running' : '') + (selected.includes(name) ? ' selected' : '');
    card.dataset.name = name;
    card.innerHTML = `
      <input type="checkbox" ${selected.includes(name) ? 'checked' : ''}>
      <div class="dot ${running ? 'running' : ''}"></div>
      <span class="student-name" title="${name}">${name}</span>
      ${running ? '<span style="font-size:.7rem;color:#4ade80;flex-shrink:0">●</span>' : ''}
      <button class="remove-student-btn" title="Remove from class" onclick="removeStudentUI('${currentClass.replace(/'/g,"\\'")}','${name.replace(/'/g,"\\'")}',event)">✕</button>
    `;
    card.addEventListener('click', () => {
      const cb = card.querySelector('input');
      cb.checked = !cb.checked;
      card.classList.toggle('selected', cb.checked);
      updateCount();
    });
    grid.appendChild(card);
  });
  updateCount();
  renderActiveTable();
}

function getSelected() {
  return [...document.querySelectorAll('.student-card')]
    .filter(c => c.querySelector('input').checked)
    .map(c => c.dataset.name);
}

function updateCount() {
  const sel   = getSelected().length;
  const total = (roster[currentClass] || []).length;
  document.getElementById('selectedCount').textContent = `${sel} selected / ${total} total`;
}

function selectAll()  {
  document.querySelectorAll('.student-card').forEach(c => {
    c.querySelector('input').checked = true;
    c.classList.add('selected');
  });
  updateCount();
}

function selectNone() {
  document.querySelectorAll('.student-card').forEach(c => {
    c.querySelector('input').checked = false;
    c.classList.remove('selected');
  });
  updateCount();
}

function setStatus(msg) { document.getElementById('statusbar').textContent = msg; }

function showErrors(errors) {
  const box = document.getElementById('errorBox');
  if (!errors.length) { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.innerHTML = errors.map(e => `⚠ <b>${e.student_id}</b>: ${e.error}`).join('<br>');
}

function showToast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}

function fmtTTL(secs) {
  if (secs <= 0) return 'expired';
  if (secs >= 86400) return Math.floor(secs/86400) + 'd ' + Math.floor((secs%86400)/3600) + 'h';
  return Math.floor(secs/3600) + 'h ' + Math.floor((secs%3600)/60) + 'm';
}

// ── session actions ───────────────────────────────────────────────────────────
async function startSelected() {
  const students = getSelected();
  if (!students.length) return setStatus('⚠ No students selected.');
  const ttl_type = document.getElementById('ttlType').value;
  setStatus(`⏳ Starting ${students.length} container(s)…`);
  const res  = await fetch('/api/session/start', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ students, ttl_type })
  });
  const data = await res.json();
  data.started.forEach(s => {
    sessionLinks[s.student_id] = s.url;
    sessionMeta[s.student_id]  = { ttl_type: s.ttl_type, ttl_remaining: s.ttl_seconds };
    runningSet.add(s.student_id);
  });
  showErrors(data.errors);
  setStatus(`✅ Started ${data.started.length} container(s)${data.errors.length ? `, ${data.errors.length} error(s)` : ''}.`);
  renderGrid();
}

async function stopSelected() {
  const students = getSelected();
  if (!students.length) return setStatus('⚠ No students selected.');
  setStatus(`⏳ Stopping ${students.length} container(s)…`);
  const res  = await fetch('/api/session/stop', {
    method: 'DELETE', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ students })
  });
  const data = await res.json();
  data.stopped.forEach(s => { runningSet.delete(s); delete sessionLinks[s]; delete sessionMeta[s]; });
  showErrors(data.errors);
  setStatus(`✅ Stopped ${data.stopped.length} container(s).`);
  renderGrid();
}

async function stopAll() {
  const all = [...runningSet];
  if (!all.length) return setStatus('⚠ No running containers.');
  setStatus(`⏳ Stopping all ${all.length} container(s)…`);
  const res  = await fetch('/api/session/stop', {
    method: 'DELETE', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ students: all })
  });
  const data = await res.json();
  runningSet.clear(); sessionLinks = {}; sessionMeta = {};
  showErrors(data.errors);
  setStatus(`✅ Stopped ${data.stopped.length} container(s).`);
  renderGrid();
}

async function redeploySelected() {
  const students = getSelected();
  if (!students.length) return setStatus('⚠ No students selected.');
  const ttl_type = document.getElementById('ttlType').value;
  setStatus(`⏳ Redeploying ${students.length} container(s)…`);
  const res  = await fetch('/api/session/redeploy', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ students, ttl_type })
  });
  const data = await res.json();
  data.redeployed.forEach(s => {
    sessionLinks[s.student_id] = s.url;
    sessionMeta[s.student_id]  = { ttl_type: s.ttl_type, ttl_remaining: s.ttl_seconds };
    runningSet.add(s.student_id);
  });
  showErrors(data.errors);
  setStatus(`✅ Redeployed ${data.redeployed.length} container(s).`);
  renderGrid();
}

async function stopOne(name) {
  setStatus(`⏳ Stopping ${name}…`);
  const res  = await fetch('/api/session/stop', {
    method: 'DELETE', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ students: [name] })
  });
  const data = await res.json();
  if (data.stopped.includes(name)) {
    runningSet.delete(name); delete sessionLinks[name]; delete sessionMeta[name];
    setStatus(`✅ Stopped ${name}.`);
  }
  renderGrid();
}

async function redeployOne(name) {
  const ttl_type = document.getElementById('ttlType').value;
  setStatus(`⏳ Redeploying ${name}…`);
  const res  = await fetch('/api/session/redeploy', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ students: [name], ttl_type })
  });
  const data = await res.json();
  data.redeployed.forEach(s => {
    sessionLinks[s.student_id] = s.url;
    sessionMeta[s.student_id]  = { ttl_type: s.ttl_type, ttl_remaining: s.ttl_seconds };
    runningSet.add(s.student_id);
  });
  showErrors(data.errors);
  setStatus(`✅ Redeployed ${name}.`);
  renderGrid();
}

// ── active table ──────────────────────────────────────────────────────────────
function renderActiveTable() {
  const tbody   = document.getElementById('activeBody');
  const section = document.getElementById('activeSection');
  const all     = roster[currentClass] || [];
  const active  = all.filter(s => runningSet.has(s));
  if (!active.length) { section.style.display = 'none'; return; }
  section.style.display = 'block';
  tbody.innerHTML = '';
  active.forEach(name => {
    const url  = sessionLinks[name] || '';
    const meta = sessionMeta[name]  || {};
    tbody.innerHTML += `
      <tr>
        <td><b>${name}</b></td>
        <td><span class="badge ${meta.ttl_type || ''}">${meta.ttl_type || '—'}</span></td>
        <td style="font-variant-numeric:tabular-nums">${meta.ttl_remaining !== undefined ? fmtTTL(meta.ttl_remaining) : '—'}</td>
        <td>
          ${url ? `<button class="copy-btn" onclick="copyLink('${name}', this)">Copy link</button>` : '—'}
        </td>
        <td>
          <button class="act-btn" onclick="redeployOne('${name}')">↺ Redeploy</button>
          <button class="act-btn red" onclick="stopOne('${name}')">■ Stop</button>
        </td>
      </tr>`;
  });
}

function copyLink(name, btn) {
  const url = sessionLinks[name];
  if (!url) return;
  navigator.clipboard.writeText(url).then(() => {
    btn.textContent = 'Copied!';
    btn.classList.add('copied');
    showToast(`✓ Link copied for ${name}`);
    setTimeout(() => { btn.textContent = 'Copy link'; btn.classList.remove('copied'); }, 2000);
  });
}

init();
</script>
</body>
</html>""")
