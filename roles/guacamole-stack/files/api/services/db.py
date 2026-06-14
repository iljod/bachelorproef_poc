import psycopg2
from config import POSTGRES_DSN


def _db_conn():
    return psycopg2.connect(POSTGRES_DSN)


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
