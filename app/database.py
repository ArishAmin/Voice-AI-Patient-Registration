import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from app.schemas import PatientCreate, PatientUpdate


PATIENT_COLUMNS = (
    "patient_id", "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "email", "address_line_1", "address_line_2", "city", "state", "zip_code",
    "insurance_provider", "insurance_member_id", "preferred_language",
    "emergency_contact_name", "emergency_contact_phone", "created_at", "updated_at",
    "deleted_at",
)


class Database:
    def __init__(self, path: str):
        self.path = path
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS patients (
                    patient_id TEXT PRIMARY KEY,
                    first_name TEXT NOT NULL CHECK(length(first_name) BETWEEN 1 AND 50),
                    last_name TEXT NOT NULL CHECK(length(last_name) BETWEEN 1 AND 50),
                    date_of_birth TEXT NOT NULL CHECK(date(date_of_birth) IS NOT NULL),
                    sex TEXT NOT NULL CHECK(sex IN ('Male','Female','Other','Decline to Answer')),
                    phone_number TEXT NOT NULL CHECK(length(phone_number) = 10),
                    email TEXT,
                    address_line_1 TEXT NOT NULL,
                    address_line_2 TEXT,
                    city TEXT NOT NULL CHECK(length(city) BETWEEN 1 AND 100),
                    state TEXT NOT NULL CHECK(length(state) = 2),
                    zip_code TEXT NOT NULL,
                    insurance_provider TEXT,
                    insurance_member_id TEXT,
                    preferred_language TEXT NOT NULL DEFAULT 'English',
                    emergency_contact_name TEXT,
                    emergency_contact_phone TEXT CHECK(emergency_contact_phone IS NULL OR length(emergency_contact_phone) = 10),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_patients_phone ON patients(phone_number);
                CREATE INDEX IF NOT EXISTS idx_patients_name ON patients(last_name);
                CREATE TABLE IF NOT EXISTS call_sessions (
                    call_sid TEXT PRIMARY KEY,
                    caller_phone TEXT,
                    state_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS call_transcripts (
                    transcript_id TEXT PRIMARY KEY,
                    call_sid TEXT NOT NULL,
                    patient_id TEXT,
                    speaker TEXT NOT NULL CHECK(speaker IN ('caller','agent')),
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(patient_id) REFERENCES patients(patient_id)
                );
            """)

    @staticmethod
    def _patient(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row else None

    def list_patients(self, filters: dict[str, str]) -> list[dict]:
        clauses = ["deleted_at IS NULL"]
        values: list[str] = []
        for key in ("last_name", "date_of_birth", "phone_number"):
            if filters.get(key):
                clauses.append(f"{key} = ?" if key != "last_name" else "lower(last_name) = lower(?)")
                values.append(filters[key])
        sql = f"SELECT {', '.join(PATIENT_COLUMNS)} FROM patients WHERE {' AND '.join(clauses)} ORDER BY created_at DESC"
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, values).fetchall()]

    def get_patient(self, patient_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                f"SELECT {', '.join(PATIENT_COLUMNS)} FROM patients WHERE patient_id = ? AND deleted_at IS NULL",
                (patient_id,),
            ).fetchone()
            return self._patient(row)

    def find_by_phone(self, phone_number: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                f"SELECT {', '.join(PATIENT_COLUMNS)} FROM patients WHERE phone_number = ? AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 1",
                (phone_number,),
            ).fetchone()
            return self._patient(row)

    def create_patient(self, patient: PatientCreate) -> dict:
        now = datetime.now(UTC).isoformat()
        record = patient.model_dump(mode="json") | {
            "patient_id": str(uuid4()), "created_at": now, "updated_at": now, "deleted_at": None,
        }
        columns = list(record)
        with self.connect() as db:
            db.execute(
                f"INSERT INTO patients ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [record[column] for column in columns],
            )
        return record

    def update_patient(self, patient_id: str, changes: PatientUpdate) -> dict | None:
        existing = self.get_patient(patient_id)
        if not existing:
            return None
        values = changes.model_dump(exclude_unset=True, mode="json")
        if not values:
            return existing
        values["updated_at"] = datetime.now(UTC).isoformat()
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.connect() as db:
            db.execute(
                f"UPDATE patients SET {assignments} WHERE patient_id = ? AND deleted_at IS NULL",
                [*values.values(), patient_id],
            )
        return self.get_patient(patient_id)

    def soft_delete_patient(self, patient_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE patients SET deleted_at = ?, updated_at = ? WHERE patient_id = ? AND deleted_at IS NULL",
                (now, now, patient_id),
            )
            return cursor.rowcount == 1

    def save_session(self, call_sid: str, caller_phone: str | None, state: dict) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as db:
            db.execute("""
                INSERT INTO call_sessions(call_sid, caller_phone, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(call_sid) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at
            """, (call_sid, caller_phone, json.dumps(state), now, now))

    def load_session(self, call_sid: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT state_json FROM call_sessions WHERE call_sid = ?", (call_sid,)).fetchone()
            return json.loads(row["state_json"]) if row else None

    def load_recent_session_by_caller(self, caller_phone: str) -> dict | None:
        with self.connect() as db:
            rows = db.execute(
                "SELECT state_json FROM call_sessions WHERE caller_phone = ? ORDER BY updated_at DESC LIMIT 5",
                (caller_phone,),
            ).fetchall()
            for row in rows:
                state = json.loads(row["state_json"])
                if state.get("stage") != "completed":
                    return state
            return None

    def add_transcript(self, call_sid: str, speaker: str, text: str, patient_id: str | None = None) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO call_transcripts VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid4()), call_sid, patient_id, speaker, text, datetime.now(UTC).isoformat()),
            )

