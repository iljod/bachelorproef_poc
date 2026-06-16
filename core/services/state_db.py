"""Roster and session store for both deployments, in one file.

Both backends expose the exact same functions so the shared routers and
orchestrator work unchanged on either. They differ only in where state lives:

- Kubernetes: Postgres (CloudNativePG), because the API is scaled to several
  replicas that must all see the same roster and sessions.
- Docker Compose: an in-process dict, because that deployment runs a single API
  container; a restart simply starts again from the seed roster below.
"""
from config import PLATFORM

# ===========================================================================
# Kubernetes backend: shared Postgres database.
# ===========================================================================
if PLATFORM == "kubernetes":
    import psycopg2.extras

    from services.db import _db_conn  # same connection (DSN or libpq PG* env)

    def class_exists(class_name: str) -> bool:
        with _db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM lab.classes WHERE class_name = %s", (class_name,))
                return cur.fetchone() is not None

    def list_roster() -> dict:
        with _db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c.class_name, s.student_id
                    FROM lab.classes c
                    LEFT JOIN lab.students s USING (class_name)
                    ORDER BY c.class_name, s.student_id
                    """
                )
                roster: dict[str, list[str]] = {}
                for class_name, student_id in cur.fetchall():
                    roster.setdefault(class_name, [])
                    if student_id is not None:
                        roster[class_name].append(student_id)
                return roster

    def add_class(class_name: str) -> bool:
        with _db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO lab.classes (class_name) VALUES (%s) ON CONFLICT DO NOTHING RETURNING 1",
                    (class_name,),
                )
                created = cur.fetchone() is not None
                conn.commit()
                return created

    def remove_class(class_name: str) -> bool:
        with _db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM lab.classes WHERE class_name = %s RETURNING 1",
                    (class_name,),
                )
                deleted = cur.fetchone() is not None
                conn.commit()
                return deleted

    def add_student(class_name: str, student_id: str) -> bool:
        with _db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lab.students (class_name, student_id) VALUES (%s, %s)
                    ON CONFLICT DO NOTHING RETURNING 1
                    """,
                    (class_name, student_id),
                )
                added = cur.fetchone() is not None
                conn.commit()
                return added

    def remove_student(class_name: str, student_id: str) -> bool:
        with _db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM lab.students WHERE class_name = %s AND student_id = %s RETURNING 1",
                    (class_name, student_id),
                )
                removed = cur.fetchone() is not None
                conn.commit()
                return removed

    def count_students(class_name: str) -> int:
        with _db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM lab.students WHERE class_name = %s", (class_name,))
                return cur.fetchone()[0]

    def list_all_sessions() -> list:
        with _db_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT student_id, container, url, ttl_type, ttl_seconds, started_at FROM lab.sessions"
                )
                return [dict(row) for row in cur.fetchall()]

    def upsert_session(student_id, container, url, ttl_type, ttl_seconds, started_at) -> None:
        with _db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO lab.sessions (student_id, container, url, ttl_type, ttl_seconds, started_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (student_id) DO UPDATE SET
                        container   = EXCLUDED.container,
                        url         = EXCLUDED.url,
                        ttl_type    = EXCLUDED.ttl_type,
                        ttl_seconds = EXCLUDED.ttl_seconds,
                        started_at  = EXCLUDED.started_at
                    """,
                    (student_id, container, url, ttl_type, ttl_seconds, started_at),
                )
                conn.commit()

    def delete_session(student_id: str) -> None:
        with _db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM lab.sessions WHERE student_id = %s", (student_id,))
                conn.commit()

# ===========================================================================
# Docker Compose backend: single-process in-memory store.
# ===========================================================================
else:
    import threading

    _lock = threading.Lock()

    _classes: dict[str, list[str]] = {
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

    _sessions: dict[str, dict] = {}

    def class_exists(class_name: str) -> bool:
        with _lock:
            return class_name in _classes

    def list_roster() -> dict:
        with _lock:
            return {name: sorted(_classes[name]) for name in sorted(_classes)}

    def add_class(class_name: str) -> bool:
        with _lock:
            if class_name in _classes:
                return False
            _classes[class_name] = []
            return True

    def remove_class(class_name: str) -> bool:
        with _lock:
            return _classes.pop(class_name, None) is not None

    def add_student(class_name: str, student_id: str) -> bool:
        with _lock:
            students = _classes.setdefault(class_name, [])
            if student_id in students:
                return False
            students.append(student_id)
            return True

    def remove_student(class_name: str, student_id: str) -> bool:
        with _lock:
            students = _classes.get(class_name, [])
            if student_id not in students:
                return False
            students.remove(student_id)
            return True

    def count_students(class_name: str) -> int:
        with _lock:
            return len(_classes.get(class_name, []))

    def list_all_sessions() -> list:
        with _lock:
            return [dict(row) for row in _sessions.values()]

    def upsert_session(student_id, container, url, ttl_type, ttl_seconds, started_at) -> None:
        with _lock:
            _sessions[student_id] = {
                "student_id":  student_id,
                "container":   container,
                "url":         url,
                "ttl_type":    ttl_type,
                "ttl_seconds": ttl_seconds,
                "started_at":  started_at,
            }

    def delete_session(student_id: str) -> None:
        with _lock:
            _sessions.pop(student_id, None)
