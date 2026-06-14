import os

JSON_SECRET_KEY = os.getenv("JSON_SECRET_KEY", "00000000000000000000000000000000")
STUDENT_IMAGE   = os.getenv("STUDENT_IMAGE",   "student-image:latest")
LAB_NETWORK     = os.getenv("LAB_NETWORK",     "lab-students")
PUBLIC_URL      = os.getenv("PUBLIC_URL",      "https://localhost/guacamole")
POSTGRES_DSN    = os.getenv("POSTGRES_DSN",    "")
CLASS_TTL       = int(os.getenv("CLASS_TTL_SECONDS",    "7200"))
HOMEWORK_TTL    = int(os.getenv("HOMEWORK_TTL_SECONDS", "604800"))

SSH_PORT        = 22
SSH_USER        = "labuser"
SSH_PASS        = "labpass"
CONNECTION_NAME = "terminal"
