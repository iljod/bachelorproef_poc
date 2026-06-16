import os

import psycopg2


def _db_conn():
    # Docker Compose passes a full DSN via POSTGRES_DSN. On Kubernetes the
    # CloudNativePG secret is injected as libpq's PGHOST/PGUSER/PGPASSWORD/...
    # env vars, so connect() with no DSN picks those up. One function, both.
    dsn = os.getenv("POSTGRES_DSN")
    return psycopg2.connect(dsn) if dsn else psycopg2.connect()


def create_db_user(student_id: str) -> None:
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO guacamole_entity (name, type) VALUES (%s, 'USER') ON CONFLICT DO NOTHING",
                (student_id,),
            )
            cur.execute(
                """
                INSERT INTO guacamole_user (entity_id, password_hash, password_salt, password_date)
                SELECT entity_id, '\\x00'::bytea, '\\x00'::bytea, NOW()
                FROM guacamole_entity WHERE name = %s AND type = 'USER'
                ON CONFLICT DO NOTHING
                """,
                (student_id,),
            )
            conn.commit()


def delete_db_user(student_id: str) -> None:
    with _db_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM guacamole_entity WHERE name = %s AND type = 'USER'",
                (student_id,),
            )
            conn.commit()
