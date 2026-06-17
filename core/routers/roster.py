import csv
import io

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from models import AddClassRequest, AddStudentRequest
from services import state_db

router = APIRouter()


@router.get("")
def get_roster():
    return state_db.list_roster()


@router.post("/class")
def add_class(req: AddClassRequest):
    name = req.class_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="class_name cannot be empty")
    if not state_db.add_class(name):
        raise HTTPException(status_code=409, detail=f"Class '{name}' already exists")
    return {"created": name, "roster": state_db.list_roster()}


@router.delete("/class/{class_name}")
def remove_class(class_name: str):
    if not state_db.remove_class(class_name):
        raise HTTPException(status_code=404, detail=f"Class '{class_name}' not found")
    return {"deleted": class_name, "roster": state_db.list_roster()}


@router.post("/class/{class_name}/student")
def add_student(class_name: str, req: AddStudentRequest):
    if not state_db.class_exists(class_name):
        raise HTTPException(status_code=404, detail=f"Class '{class_name}' not found")
    sid = req.student_id.strip().lower()
    if not sid:
        raise HTTPException(status_code=400, detail="student_id cannot be empty")
    if not state_db.add_student(class_name, sid):
        raise HTTPException(status_code=409, detail=f"'{sid}' is already in '{class_name}'")
    return {"added": sid, "class": class_name, "total": state_db.count_students(class_name)}


@router.delete("/class/{class_name}/student/{student_id}")
def remove_student(class_name: str, student_id: str):
    if not state_db.class_exists(class_name):
        raise HTTPException(status_code=404, detail=f"Class '{class_name}' not found")
    if not state_db.remove_student(class_name, student_id):
        raise HTTPException(status_code=404, detail=f"'{student_id}' not found in '{class_name}'")
    return {"removed": student_id, "class": class_name, "total": state_db.count_students(class_name)}


@router.post("/import")
async def import_roster_csv(file: UploadFile = File(...)):
    """
    Import classes and students from a CSV file.

    Expected format (with or without header row):
        class_name,student_id
        Class A,alice
        Class B,bob

    Creates the class if it does not exist yet.
    Silently skips duplicate students.
    Returns a summary of additions and any row-level errors.
    """
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=400, detail="Only .csv files are accepted")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded")

    reader  = csv.reader(io.StringIO(text))
    added:   dict[str, list[str]] = {}
    skipped: list[str] = []
    errors:  list[str] = []

    for i, row in enumerate(reader):
        if not any(cell.strip() for cell in row):
            continue
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

        state_db.add_class(class_name)
        if state_db.add_student(class_name, student_id):
            added.setdefault(class_name, []).append(student_id)
        else:
            skipped.append(f"{class_name}/{student_id}")

    return {
        "added":       added,
        "total_added": sum(len(v) for v in added.values()),
        "skipped":     skipped,
        "errors":      errors,
        "roster":      state_db.list_roster(),
    }


@router.get("/export")
def export_roster_csv():
    """Download the current full roster as a CSV file."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["class_name", "student_id"])
    for class_name, students in state_db.list_roster().items():
        for s in students:
            writer.writerow([class_name, s])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=roster.csv"},
    )
