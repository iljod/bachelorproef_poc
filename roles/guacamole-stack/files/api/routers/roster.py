import csv
import io

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from models import AddClassRequest, AddStudentRequest
from state import CLASSES

router = APIRouter()


@router.get("")
def get_roster():
    return CLASSES


@router.post("/class")
def add_class(req: AddClassRequest):
    name = req.class_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="class_name cannot be empty")
    if name in CLASSES:
        raise HTTPException(status_code=409, detail=f"Class '{name}' already exists")
    CLASSES[name] = []
    return {"created": name, "roster": CLASSES}


@router.delete("/class/{class_name}")
def remove_class(class_name: str):
    if class_name not in CLASSES:
        raise HTTPException(status_code=404, detail=f"Class '{class_name}' not found")
    del CLASSES[class_name]
    return {"deleted": class_name, "roster": CLASSES}


@router.post("/class/{class_name}/student")
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


@router.delete("/class/{class_name}/student/{student_id}")
def remove_student(class_name: str, student_id: str):
    if class_name not in CLASSES:
        raise HTTPException(status_code=404, detail=f"Class '{class_name}' not found")
    if student_id not in CLASSES[class_name]:
        raise HTTPException(status_code=404, detail=f"'{student_id}' not found in '{class_name}'")
    CLASSES[class_name].remove(student_id)
    return {"removed": student_id, "class": class_name, "total": len(CLASSES[class_name])}


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

        if class_name not in CLASSES:
            CLASSES[class_name] = []

        if student_id in CLASSES[class_name]:
            skipped.append(f"{class_name}/{student_id}")
        else:
            CLASSES[class_name].append(student_id)
            added.setdefault(class_name, []).append(student_id)

    return {
        "added":       added,
        "total_added": sum(len(v) for v in added.values()),
        "skipped":     skipped,
        "errors":      errors,
        "roster":      CLASSES,
    }


@router.get("/export")
def export_roster_csv():
    """Download the current full roster as a CSV file."""
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
