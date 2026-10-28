import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("VALIDATE_TWILIO_SIGNATURES", "false")
    from app.config import get_settings
    get_settings.cache_clear()
    import app.main as main
    main.settings = get_settings()
    main.database = main.Database(main.settings.database_path)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def patient_payload():
    return {
        "first_name": "Jane",
        "last_name": "O'Neil",
        "date_of_birth": "1990-04-15",
        "sex": "Female",
        "phone_number": "4155550123",
        "email": "jane@example.com",
        "address_line_1": "42 Market Street",
        "address_line_2": "Apt 5",
        "city": "San Francisco",
        "state": "CA",
        "zip_code": "94105",
        "preferred_language": "English"
    }

