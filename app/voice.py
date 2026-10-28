import json
import logging
from datetime import date
from typing import Literal

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from app.database import Database
from app.schemas import PatientCreate, PatientUpdate, normalize_phone


logger = logging.getLogger(__name__)

REQUIRED_FIELDS = [
    "first_name", "last_name", "date_of_birth", "sex", "phone_number",
    "address_line_1", "city", "state", "zip_code",
]
FIELD_LABELS = {
    "first_name": "first name", "last_name": "last name", "date_of_birth": "date of birth",
    "sex": "sex", "phone_number": "phone number", "email": "email address",
    "address_line_1": "street address", "address_line_2": "apartment or unit",
    "city": "city", "state": "state", "zip_code": "ZIP code",
    "insurance_provider": "insurance provider", "insurance_member_id": "insurance member ID",
    "preferred_language": "preferred language", "emergency_contact_name": "emergency contact name",
    "emergency_contact_phone": "emergency contact phone",
}
ASK = {
    "first_name": "Let's start with your first and last name.",
    "last_name": "And what is your last name?",
    "date_of_birth": "What is your date of birth, including the year?",
    "sex": "What sex should I record: male, female, other, or decline to answer?",
    "phone_number": "What is the best ten-digit phone number for you?",
    "address_line_1": "What is your street address? You can include the apartment or unit too.",
    "city": "What city do you live in?",
    "state": "What is the two-letter abbreviation for your state?",
    "zip_code": "And what is your ZIP code?",
}


class VoicePatch(BaseModel):
    first_name: str | None = None
    last_name: str | None = None
    date_of_birth: str | None = None
    sex: str | None = None
    phone_number: str | None = None
    email: str | None = None
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    insurance_provider: str | None = None
    insurance_member_id: str | None = None
    preferred_language: str | None = None
    emergency_contact_name: str | None = None
    emergency_contact_phone: str | None = None


class TurnExtraction(BaseModel):
    updates: VoicePatch
    intent: Literal[
        "provide", "confirm", "deny", "restart", "skip_optional", "finish_optional",
        "update_existing", "create_new",
    ] = "provide"


SYSTEM_PROMPT = """You extract patient-registration facts from one caller utterance.
Return only the requested structured output. Use the conversation state and requested field as context.
- Extract every demographic the caller supplies, even out of order.
- Apply explicit corrections as the new value. Convert spelled names such as D-A-V-I-S to Davis.
- Normalize dates to YYYY-MM-DD, U.S. phones to 10 digits, states to 2-letter codes, and sex to exactly Male, Female, Other, or Decline to Answer.
- Use intent=confirm only when the caller confirms the complete readback; deny when they reject it.
- Use restart for requests to start over; skip_optional when they decline optional questions; finish_optional when they say they are done with optional details.
- Use update_existing or create_new only when answering the duplicate-record question.
- Never invent a value. Leave fields null when the caller did not provide them.
This is a technical demo; never request medical or payment information."""


class OpenAIExtractor:
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def extract(self, utterance: str, state: dict) -> TurnExtraction:
        context = {
            "stage": state.get("stage"),
            "requested_field": state.get("requested_field"),
            "collected": state.get("data", {}),
            "caller_utterance": utterance,
        }
        response = self.client.responses.parse(
            model=self.model,
            instructions=SYSTEM_PROMPT,
            input=json.dumps(context),
            text_format=TurnExtraction,
        )
        if response.output_parsed is None:
            raise RuntimeError("The language model did not return a usable result")
        return response.output_parsed


def new_state(caller_phone: str | None = None) -> dict:
    data: dict[str, str] = {}
    if caller_phone:
        try:
            data["phone_number"] = normalize_phone(caller_phone) or ""
        except ValueError:
            pass
    return {
        "stage": "collecting", "data": data, "requested_field": "first_name",
        "duplicate_checked": False, "existing_patient_id": None,
    }


def next_missing(data: dict) -> str | None:
    return next((field for field in REQUIRED_FIELDS if not data.get(field)), None)


def _validation_error(exc: ValidationError) -> tuple[str, str]:
    error = exc.errors()[0]
    field = str(error["loc"][0])
    message = str(error["msg"]).removeprefix("Value error, ")
    return field, message


def validate_updates(raw: dict) -> tuple[dict, tuple[str, str] | None]:
    cleaned = {key: value for key, value in raw.items() if value is not None}
    if not cleaned:
        return {}, None
    try:
        update = PatientUpdate.model_validate(cleaned)
        return update.model_dump(exclude_unset=True, mode="json"), None
    except ValidationError as exc:
        return {}, _validation_error(exc)


def confirmation_text(data: dict) -> str:
    dob = date.fromisoformat(data["date_of_birth"]).strftime("%B %d, %Y")
    phone = data["phone_number"]
    phone_spoken = f"{phone[:3]} {phone[3:6]} {phone[6:]}"
    address = ", ".join(
        part for part in [data["address_line_1"], data.get("address_line_2"), data["city"], data["state"], data["zip_code"]] if part
    )
    optional = []
    for field in ("email", "insurance_provider", "insurance_member_id", "preferred_language", "emergency_contact_name", "emergency_contact_phone"):
        if data.get(field) and not (field == "preferred_language" and data[field] == "English"):
            optional.append(f"{FIELD_LABELS[field]} {data[field]}")
    text = (
        f"Let me confirm: {data['first_name']} {data['last_name']}, born {dob}, "
        f"sex {data['sex']}, phone {phone_spoken}, and address {address}"
    )
    if optional:
        text += ". I also have " + ", ".join(optional)
    return text + ". Is all of that correct?"


def initial_prompt(db: Database, call_sid: str, caller_phone: str | None) -> str:
    state = new_state(caller_phone)
    if state["data"].get("phone_number"):
        existing = db.find_by_phone(state["data"]["phone_number"])
        if existing:
            state.update(stage="duplicate", existing_patient_id=existing["patient_id"])
            prompt = (
                f"Welcome back. It looks like we already have a record for {existing['first_name']} "
                f"{existing['last_name']}. Would you like to update that record, or create a new registration?"
            )
            db.save_session(call_sid, caller_phone, state)
            db.add_transcript(call_sid, "agent", prompt)
            return prompt
        recent = db.load_recent_session_by_caller(caller_phone)
        if recent:
            state = recent
            if state.get("stage") == "confirming":
                prompt = "Welcome back. We can continue where the call ended. " + confirmation_text(state["data"])
            elif state.get("stage") == "optional":
                prompt = "Welcome back. Would you like to add an optional detail, or should I read everything back?"
            else:
                field = next_missing(state.get("data", {})) or state.get("requested_field") or "first_name"
                prompt = "Welcome back. We can continue where the call ended. " + ASK.get(field, ASK["first_name"])
            db.save_session(call_sid, caller_phone, state)
            db.add_transcript(call_sid, "agent", prompt)
            return prompt
    prompt = "Thanks for calling CareCloud registration. I can help register you today. " + ASK["first_name"]
    db.save_session(call_sid, caller_phone, state)
    db.add_transcript(call_sid, "agent", prompt)
    return prompt


def process_turn(db: Database, call_sid: str, utterance: str, extractor) -> tuple[str, bool]:
    state = db.load_session(call_sid) or new_state()
    db.add_transcript(call_sid, "caller", utterance)
    try:
        extracted = extractor.extract(utterance, state)
    except Exception:
        logger.exception("LLM extraction failed", extra={"call_sid": call_sid})
        reply = "I'm sorry, I had trouble understanding that. Please say it once more."
        db.add_transcript(call_sid, "agent", reply)
        return reply, False

    if extracted.intent == "restart":
        state = new_state()
        reply = "No problem. I've cleared what we discussed. " + ASK["first_name"]
        db.save_session(call_sid, None, state)
        db.add_transcript(call_sid, "agent", reply)
        return reply, False

    if state["stage"] == "duplicate":
        existing = db.get_patient(state["existing_patient_id"])
        if extracted.intent == "update_existing" and existing:
            state["data"] = {key: value for key, value in existing.items() if key in VoicePatch.model_fields and value is not None}
            state["stage"] = "collecting"
            state["duplicate_checked"] = True
            reply = "Great. Tell me what information you would like to change."
        elif extracted.intent == "create_new":
            state = new_state(state["data"].get("phone_number"))
            state["duplicate_checked"] = True
            reply = "Okay, we'll create a new registration. " + ASK["first_name"]
        else:
            reply = "Please say update the existing record, or create a new registration."
        db.save_session(call_sid, None, state)
        db.add_transcript(call_sid, "agent", reply)
        return reply, False

    updates, error = validate_updates(extracted.updates.model_dump())
    if error:
        field, message = error
        state["requested_field"] = field
        reply = f"That {FIELD_LABELS.get(field, field)} {message}. Please provide it again."
        db.save_session(call_sid, None, state)
        db.add_transcript(call_sid, "agent", reply)
        return reply, False
    state["data"].update(updates)

    if state["stage"] == "confirming":
        if extracted.intent == "confirm":
            try:
                payload = PatientCreate.model_validate(state["data"])
                if state.get("existing_patient_id"):
                    patient = db.update_patient(state["existing_patient_id"], PatientUpdate.model_validate(payload.model_dump()))
                else:
                    patient = db.create_patient(payload)
                logger.info("patient registration saved", extra={"call_sid": call_sid, "patient_id": patient["patient_id"]})
                reply = f"You're all set, {patient['first_name']}. Your registration has been saved. Goodbye."
                state["stage"] = "completed"
                db.save_session(call_sid, None, state)
                db.add_transcript(call_sid, "agent", reply, patient["patient_id"])
                return reply, True
            except Exception:
                logger.exception("Patient save failed", extra={"call_sid": call_sid})
                reply = "I'm sorry, I couldn't save your registration right now. Your information has not been lost. Please try again shortly."
                db.add_transcript(call_sid, "agent", reply)
                return reply, False
        if updates:
            reply = "Thanks for the correction. " + confirmation_text(state["data"])
            db.save_session(call_sid, None, state)
            db.add_transcript(call_sid, "agent", reply)
            return reply, False
        state["stage"] = "collecting"
        state["requested_field"] = None
        reply = "Of course. Tell me which detail needs to be corrected."
        db.save_session(call_sid, None, state)
        db.add_transcript(call_sid, "agent", reply)
        return reply, False

    missing = next_missing(state["data"])
    if missing:
        state["requested_field"] = missing
        reply = ASK[missing]
    elif not state["duplicate_checked"]:
        state["duplicate_checked"] = True
        existing = db.find_by_phone(state["data"]["phone_number"])
        if existing:
            state.update(stage="duplicate", existing_patient_id=existing["patient_id"])
            reply = (
                f"It looks like we already have a record for {existing['first_name']} {existing['last_name']}. "
                "Would you like to update that record, or create a new registration?"
            )
        else:
            state["stage"] = "optional"
            reply = (
                "I have all the required details. I can also collect your email, insurance information, "
                "emergency contact, or preferred language. Would you like to provide any of those?"
            )
    elif state["stage"] == "optional":
        if extracted.intent in {"skip_optional", "finish_optional"}:
            state["stage"] = "confirming"
            reply = confirmation_text(state["data"])
        else:
            reply = "Thank you. Would you like to add another optional detail, or should I read everything back?"
    else:
        state["stage"] = "optional"
        reply = (
            "I have all the required details. I can also collect your email, insurance information, "
            "emergency contact, or preferred language. Would you like to provide any of those?"
        )

    db.save_session(call_sid, None, state)
    db.add_transcript(call_sid, "agent", reply)
    return reply, False

