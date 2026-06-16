import hashlib
import os
import secrets
import time

import psycopg2
from kubernetes import client, config as k8s_config
from kubernetes.client.rest import ApiException

NAMESPACE          = os.environ.get("NAMESPACE", "lab-platform")
JSON_SECRET_NAME   = os.environ.get("JSON_SECRET_NAME", "lab-platform-json-secret")
ADMIN_SECRET_NAME  = os.environ.get("ADMIN_SECRET_NAME", "guacamole-admin-credentials")

# The Guacamole schema is rendered at deploy time by an initContainer running
# `initdb.sh --postgresql` from the same guacamole/guacamole image version that
# gets deployed, into a shared volume. That keeps the schema in lockstep with
# the running Guacamole instead of relying on a vendored copy that can drift.
SCHEMA_PATH = os.environ.get("SCHEMA_PATH", "/schema/initdb.sql")


def wait_for_postgres(timeout: float = 120) -> None:
    deadline = time.time() + timeout
    last_exc = None
    while time.time() < deadline:
        try:
            psycopg2.connect().close()
            return
        except psycopg2.OperationalError as exc:
            last_exc = exc
            time.sleep(2)
    raise RuntimeError(f"Postgres not reachable after {timeout}s") from last_exc


def guacamole_schema_loaded(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'guacamole_entity'"
        )
        return cur.fetchone() is not None


def load_guacamole_schema(conn) -> None:
    if guacamole_schema_loaded(conn):
        print("[bootstrap] guacamole schema already present, skipping")
        return
    with open(SCHEMA_PATH) as f:
        with conn.cursor() as cur:
            cur.execute(f.read())
    conn.commit()
    print("[bootstrap] guacamole schema loaded")


def ensure_lab_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE SCHEMA IF NOT EXISTS lab")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lab.classes (
                class_name text PRIMARY KEY
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lab.students (
                class_name text NOT NULL REFERENCES lab.classes(class_name) ON DELETE CASCADE,
                student_id text NOT NULL,
                PRIMARY KEY (class_name, student_id)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lab.sessions (
                student_id  text PRIMARY KEY,
                container   text NOT NULL,
                url         text NOT NULL,
                ttl_type    text NOT NULL,
                ttl_seconds integer NOT NULL,
                started_at  double precision NOT NULL
            )
            """
        )
    conn.commit()
    print("[bootstrap] lab schema ensured")


def k8s_get_secret(core_v1, name):
    try:
        return core_v1.read_namespaced_secret(name, NAMESPACE)
    except ApiException as exc:
        if exc.status == 404:
            return None
        raise


def k8s_create_secret(core_v1, name: str, string_data: dict) -> None:
    secret = client.V1Secret(
        metadata=client.V1ObjectMeta(name=name),
        type="Opaque",
        string_data=string_data,
    )
    core_v1.create_namespaced_secret(NAMESPACE, secret)


def ensure_json_secret_key(core_v1) -> None:
    if k8s_get_secret(core_v1, JSON_SECRET_NAME):
        print(f"[bootstrap] secret/{JSON_SECRET_NAME} already exists, skipping")
        return
    key = secrets.token_hex(32)
    k8s_create_secret(core_v1, JSON_SECRET_NAME, {"json-secret-key": key})
    print(f"[bootstrap] secret/{JSON_SECRET_NAME} created")


def rotate_guacadmin_password(conn, core_v1) -> None:
    """
    Replaces the schema's well-known guacadmin/guacadmin row with a random
    password, stored as a break-glass credential Secret. Hash algorithm must
    match org.apache.guacamole.auth.jdbc.security.SHA256PasswordEncryptionService
    exactly: sha256(password_utf8_bytes + uppercase_hex(salt_bytes)).
    """
    if k8s_get_secret(core_v1, ADMIN_SECRET_NAME):
        print(f"[bootstrap] secret/{ADMIN_SECRET_NAME} already exists, skipping rotation")
        return

    password = secrets.token_urlsafe(24)
    salt     = secrets.token_bytes(32)
    pw_hash  = hashlib.sha256((password + salt.hex().upper()).encode("utf-8")).digest()

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE guacamole_user
            SET password_hash = %s,
                password_salt = %s,
                password_date = NOW()
            WHERE entity_id = (
                SELECT entity_id FROM guacamole_entity
                WHERE name = 'guacadmin' AND type = 'USER'
            )
            """,
            (pw_hash, salt),
        )
    conn.commit()

    k8s_create_secret(core_v1, ADMIN_SECRET_NAME, {"username": "guacadmin", "password": password})
    print(f"[bootstrap] guacadmin password rotated, secret/{ADMIN_SECRET_NAME} created")


def main() -> None:
    wait_for_postgres()
    conn = psycopg2.connect()
    try:
        load_guacamole_schema(conn)
        ensure_lab_schema(conn)

        k8s_config.load_incluster_config()
        core_v1 = client.CoreV1Api()

        ensure_json_secret_key(core_v1)
        rotate_guacadmin_password(conn, core_v1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
