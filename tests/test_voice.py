from app.database import Database
from app.voice import TurnExtraction, VoicePatch, initial_prompt, process_turn


class ScriptedExtractor:
    def __init__(self, turns):
        self.turns = iter(turns)

    def extract(self, _utterance, _state):
        return next(self.turns)


def extraction(intent="provide", **updates):
    return TurnExtraction(intent=intent, updates=VoicePatch(**updates))


def test_full_voice_registration_with_out_of_order_data(tmp_path):
    db = Database(str(tmp_path / "voice.db"))
    db.initialize()
    call_sid = "CA_FULL"
    assert "first and last name" in initial_prompt(db, call_sid, None)
    extractor = ScriptedExtractor([
        extraction(first_name="Jane", last_name="Davis", date_of_birth="1990-04-15"),
        extraction(sex="Female", address_line_1="42 Market Street", city="Austin", state="TX", zip_code="78701", phone_number="5125550199"),
        extraction(intent="skip_optional"),
        extraction(intent="confirm"),
    ])

    reply, done = process_turn(db, call_sid, "I'm Jane Davis, born April 15 1990", extractor)
    assert not done and "sex" in reply.lower()
    reply, done = process_turn(db, call_sid, "Female and here is the rest", extractor)
    assert not done and "also collect" in reply
    reply, done = process_turn(db, call_sid, "No thanks", extractor)
    assert not done and "Let me confirm" in reply
    reply, done = process_turn(db, call_sid, "Yes, correct", extractor)
    assert done and "all set, Jane" in reply
    assert db.find_by_phone("5125550199")["last_name"] == "Davis"


def test_invalid_field_is_reprompted(tmp_path):
    db = Database(str(tmp_path / "invalid.db"))
    db.initialize()
    call_sid = "CA_INVALID"
    initial_prompt(db, call_sid, None)
    extractor = ScriptedExtractor([extraction(phone_number="123")])
    reply, done = process_turn(db, call_sid, "My number is 123", extractor)
    assert not done
    assert "valid U.S. 10-digit" in reply


def test_restart_clears_collected_data(tmp_path):
    db = Database(str(tmp_path / "restart.db"))
    db.initialize()
    call_sid = "CA_RESTART"
    initial_prompt(db, call_sid, None)
    extractor = ScriptedExtractor([
        extraction(first_name="Jane", last_name="Davis"),
        extraction(intent="restart"),
    ])
    process_turn(db, call_sid, "Jane Davis", extractor)
    reply, _ = process_turn(db, call_sid, "Let's start over", extractor)
    assert "cleared" in reply
    assert db.load_session(call_sid)["data"] == {}


def test_correction_during_confirmation_is_read_back(tmp_path):
    db = Database(str(tmp_path / "correction.db"))
    db.initialize()
    call_sid = "CA_CORRECT"
    state = {
        "stage": "confirming",
        "data": {
            "first_name": "Jane", "last_name": "Davies", "date_of_birth": "1990-04-15",
            "sex": "Female", "phone_number": "5125550199", "address_line_1": "42 Market Street",
            "city": "Austin", "state": "TX", "zip_code": "78701", "preferred_language": "English",
        },
        "requested_field": None, "duplicate_checked": True, "existing_patient_id": None,
    }
    db.save_session(call_sid, None, state)
    extractor = ScriptedExtractor([extraction(intent="deny", last_name="Davis")])
    reply, done = process_turn(db, call_sid, "Davis, not Davies", extractor)
    assert not done
    assert "Thanks for the correction" in reply
    assert "Jane Davis" in reply

