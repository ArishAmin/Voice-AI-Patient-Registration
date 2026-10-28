import logging
from contextlib import asynccontextmanager
from datetime import date
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import VoiceResponse

from app.config import Settings, get_settings
from app.database import Database
from app.schemas import Envelope, PatientCreate, PatientUpdate, normalize_phone
from app.voice import OpenAIExtractor, initial_prompt, process_turn


settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")
database = Database(settings.database_path)
templates = Jinja2Templates(directory="app/templates")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.ensure_data_directory()
    database.initialize()
    yield


app = FastAPI(title="CareCloud Voice Patient Registration", version="1.0.0", lifespan=lifespan)


def envelope(data=None, error=None, status_code: int = 200):
    return JSONResponse({"data": data, "error": error}, status_code=status_code)


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(map(str, item["loc"][1:])), "message": item["msg"]} for item in exc.errors()]
    return envelope(error={"code": "validation_error", "details": errors}, status_code=422)


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, exc: HTTPException):
    return envelope(error={"code": "http_error", "message": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unknown_error_handler(_: Request, exc: Exception):
    logging.getLogger(__name__).exception("Unhandled API error", exc_info=exc)
    return envelope(error={"code": "internal_error", "message": "An unexpected error occurred"}, status_code=500)


def valid_twilio_request(request: Request, form: dict, config: Settings) -> bool:
    if not config.validate_twilio_signatures:
        return True
    if not config.twilio_auth_token:
        return False
    external_url = config.app_base_url.rstrip("/") + request.url.path
    if request.url.query:
        external_url += "?" + request.url.query
    return RequestValidator(config.twilio_auth_token).validate(
        external_url, form, request.headers.get("X-Twilio-Signature", "")
    )


def twiml_prompt(prompt: str, *, hangup: bool = False) -> Response:
    voice = VoiceResponse()
    if hangup:
        voice.say(prompt, voice="Polly.Joanna-Neural", language="en-US")
        voice.hangup()
    else:
        gather = voice.gather(
            input="speech", action="/voice/turn", method="POST", language="en-US",
            speech_model="phone_call", speech_timeout=2, action_on_empty_result=True,
        )
        gather.say(prompt, voice="Polly.Joanna-Neural", language="en-US")
        voice.say("I didn't hear anything. Please call back when you're ready.", voice="Polly.Joanna-Neural")
        voice.hangup()
    return Response(str(voice), media_type="application/xml")


@app.get("/health", response_model=Envelope)
def health():
    return {"data": {"status": "ok"}, "error": None}


@app.get("/patients", response_model=Envelope)
def list_patients(
    last_name: str | None = Query(default=None, max_length=50),
    date_of_birth: date | None = None,
    phone_number: str | None = None,
):
    filters = {"last_name": last_name, "date_of_birth": date_of_birth.isoformat() if date_of_birth else None, "phone_number": phone_number}
    if phone_number:
        try:
            filters["phone_number"] = normalize_phone(phone_number)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return {"data": database.list_patients({key: value for key, value in filters.items() if value}), "error": None}


@app.get("/patients/{patient_id}", response_model=Envelope)
def get_patient(patient_id: UUID):
    patient = database.get_patient(str(patient_id))
    if not patient:
        raise HTTPException(404, "Patient not found")
    return {"data": patient, "error": None}


@app.post("/patients", response_model=Envelope, status_code=201)
def create_patient(patient: PatientCreate):
    return {"data": database.create_patient(patient), "error": None}


@app.put("/patients/{patient_id}", response_model=Envelope)
def update_patient(patient_id: UUID, changes: PatientUpdate):
    patient = database.update_patient(str(patient_id), changes)
    if not patient:
        raise HTTPException(404, "Patient not found")
    return {"data": patient, "error": None}


@app.delete("/patients/{patient_id}", response_model=Envelope)
def delete_patient(patient_id: UUID):
    if not database.soft_delete_patient(str(patient_id)):
        raise HTTPException(404, "Patient not found")
    return {"data": {"patient_id": str(patient_id), "deleted": True}, "error": None}


@app.post("/voice/incoming")
async def voice_incoming(request: Request, config: Settings = Depends(get_settings)):
    form = dict(await request.form())
    if not valid_twilio_request(request, form, config):
        raise HTTPException(403, "Invalid Twilio signature")
    call_sid = str(form.get("CallSid", ""))
    if not call_sid:
        raise HTTPException(400, "CallSid is required")
    return twiml_prompt(initial_prompt(database, call_sid, form.get("From")))


@app.post("/voice/turn")
async def voice_turn(request: Request, config: Settings = Depends(get_settings)):
    form = dict(await request.form())
    if not valid_twilio_request(request, form, config):
        raise HTTPException(403, "Invalid Twilio signature")
    call_sid = str(form.get("CallSid", ""))
    speech = str(form.get("SpeechResult", "")).strip()
    if not call_sid:
        raise HTTPException(400, "CallSid is required")
    if not speech:
        return twiml_prompt("I didn't catch that. Please say it again.")
    try:
        extractor = OpenAIExtractor(config.openai_api_key, config.openai_model)
    except RuntimeError:
        logging.getLogger(__name__).error("OPENAI_API_KEY is not configured")
        return twiml_prompt("The registration service is temporarily unavailable. Please try again later.", hangup=True)
    reply, done = process_turn(database, call_sid, speech, extractor)
    return twiml_prompt(reply, hangup=done)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html")

