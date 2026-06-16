import os

# Which deployment this process runs in: "docker" or "kubernetes". Every
# deployment sets it (baked into each image as an ENV, and set again in the
# compose file / Helm chart), and the orchestrator and state_db modules branch
# on it. There is deliberately no default: an unset or misspelled value is a
# misconfiguration, and silently picking a platform would be worse than failing.
PLATFORM = os.getenv("LAB_PLATFORM")
if PLATFORM not in ("docker", "kubernetes"):
    raise RuntimeError(
        f"LAB_PLATFORM must be 'docker' or 'kubernetes', got {PLATFORM!r}. "
        "It is normally set by the deployment; set it explicitly to run the app directly."
    )

# --- shared by both deployments ---
JSON_SECRET_KEY = os.getenv("JSON_SECRET_KEY", "00000000000000000000000000000000")
STUDENT_IMAGE   = os.getenv("STUDENT_IMAGE",   "student-image:latest")
PUBLIC_URL      = os.getenv("PUBLIC_URL",      "https://localhost/guacamole")
CLASS_TTL       = int(os.getenv("CLASS_TTL_SECONDS",    "7200"))
HOMEWORK_TTL    = int(os.getenv("HOMEWORK_TTL_SECONDS", "604800"))

SSH_PORT        = 22
SSH_USER        = "labuser"
SSH_PASS        = "labpass"
CONNECTION_NAME = "terminal"

# --- Docker Compose only ---
LAB_NETWORK          = os.getenv("LAB_NETWORK", "lab-students")
STUDENT_MEMORY_LIMIT = os.getenv("STUDENT_MEMORY_LIMIT", "512m")
STUDENT_CPU_QUOTA    = int(os.getenv("STUDENT_CPU_QUOTA",  "50000"))
STUDENT_CPU_PERIOD   = int(os.getenv("STUDENT_CPU_PERIOD", "100000"))

# --- Kubernetes only ---
STUDENT_NAMESPACE   = os.getenv("STUDENT_NAMESPACE",   "lab-students")
STUDENT_MEMORY      = os.getenv("STUDENT_MEMORY_LIMIT", "512Mi")
STUDENT_CPU_REQUEST = os.getenv("STUDENT_CPU_REQUEST",  "250m")
STUDENT_CPU_LIMIT   = os.getenv("STUDENT_CPU_LIMIT",    "500m")
