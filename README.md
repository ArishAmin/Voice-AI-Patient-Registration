# Voice AI Patient Registration

A complete implementation: callers register by speaking naturally to a Twilio number, OpenAI extracts and normalizes demographic details, FastAPI validates and persists them in SQLite, and reviewers can query the REST API or use the web dashboard.

> This is a demonstration system, not a HIPAA-compliant production service. Never enter real patient or protected health information.

## Status

- Implemented and locally tested: REST API, SQLite persistence, validation, soft deletion, call state, LLM extraction contract, TwiML generation, correction/confirmation flow, duplicate detection, call resumption, transcripts, dashboard, Docker packaging.
- Not yet verified: a deployed public URL, real Twilio phone number, live OpenAI response, or real end-to-end phone call. Those require the candidate's accounts and credentials.

## Architecture

```text
Caller
  |
  v
Twilio phone number -- speech-to-text / text-to-speech
  |
  | POST /voice/incoming and /voice/turn
  v
FastAPI voice orchestrator -- OpenAI structured extraction
  |                                  |
  | validation + confirmed write     | understands corrections,
  v                                  | out-of-order facts, intent
SQLite <-----------------------------+
  |
  +-- REST API (/patients)
  +-- Dashboard (/dashboard)
  +-- call sessions and transcripts
```

### Why this stack

- **Twilio `<Gather input="speech">`** provides a real U.S. number, speech recognition, and speech synthesis without building STT/TTS infrastructure.
- **OpenAI Responses structured outputs** turns varied speech into a typed demographic patch and intent. The deterministic orchestrator owns validation, required-field coverage, confirmation, and writes; the model cannot bypass those rules.
- **FastAPI + Pydantic** gives clear REST endpoints, OpenAPI docs, and server-side validation.
- **SQLite in WAL mode** is the simplest persistent database for a short assessment. A mounted volume is mandatory in cloud hosting. PostgreSQL is the natural production upgrade.

## Conversation behavior

The agent accepts multiple and out-of-order fields in one answer, normalizes spelled names/dates/phones/states, and asks only for missing required values. Invalid values are rejected by Pydantic before entering session state. After required fields, it offers optional insurance, emergency contact, language, and email collection. It reads the full record back and writes only after explicit confirmation.

Supported resilience behavior:

- specific re-prompts for invalid fields;
- correction at any point, including during final confirmation;
- "start over" clears the in-progress session;
- in-progress state is stored after each turn and resumed when the same caller reconnects;
- a matching phone number triggers the returning-patient update choice;
- database/LLM failures produce a spoken retry message instead of silence;
- every caller and agent turn is persisted in `call_transcripts` and final saves are logged.

## Local setup

Requirements: Python 3.12+, an OpenAI API key, and (for real calls) a Twilio account with a voice-capable U.S. number.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
copy .env.example .env  # Windows; use `cp` on macOS/Linux
```

Set `OPENAI_API_KEY` in `.env`, then run:

```bash
uvicorn app.main:app --reload
```

Open:

- API documentation: `http://localhost:8000/docs`
- Dashboard: `http://localhost:8000/dashboard`
- Health check: `http://localhost:8000/health`

Run tests:

```bash
python -m pytest -q tests
```

## Environment variables

| Variable | Required | Purpose |
|---|---:|---|
| `APP_BASE_URL` | Production | Exact public HTTPS origin, with no trailing slash. Used for Twilio signature verification. |
| `DATABASE_PATH` | Yes | SQLite path. Use `/data/patients.db` on a persistent cloud volume. |
| `OPENAI_API_KEY` | Voice calls | OpenAI credential. Never commit it. |
| `OPENAI_MODEL` | No | Defaults to `gpt-4o-mini`. Must support structured outputs. |
| `TWILIO_AUTH_TOKEN` | Production | Validates Twilio webhook signatures. |
| `VALIDATE_TWILIO_SIGNATURES` | Production | Set `true` after the public URL is final. |
| `LOG_LEVEL` | No | Defaults to `INFO`. |

## REST API

All JSON responses use `{ "data": ..., "error": null }`, or `{ "data": null, "error": ... }`.

| Method | Endpoint | Behavior |
|---|---|---|
| `GET` | `/patients` | List active patients; filters: `last_name`, `date_of_birth`, `phone_number` |
| `GET` | `/patients/{uuid}` | Get one active patient |
| `POST` | `/patients` | Create and return a patient (`201`) |
| `PUT` | `/patients/{uuid}` | Partial update |
| `DELETE` | `/patients/{uuid}` | Soft delete; sets `deleted_at` |

Example create request:

```json
{
  "first_name": "Jane",
  "last_name": "Davis",
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
```

## Deploy to Railway

1. Push this folder to a GitHub repository.
2. Create a Railway project from that repository; the included `Dockerfile` is detected automatically.
3. Add a Railway persistent volume mounted at `/data`. Without this, SQLite data can disappear when the container is replaced.
4. Add the environment variables above. Initially leave `VALIDATE_TWILIO_SIGNATURES=false`.
5. Generate a public domain, then set `APP_BASE_URL=https://your-domain`.
6. Verify `https://your-domain/health`, `/docs`, `/dashboard`, and a create/list/restart persistence cycle.
7. Set `VALIDATE_TWILIO_SIGNATURES=true` and redeploy.

## Connect Twilio

1. In Twilio, buy a voice-capable U.S. local number.
2. On that number's Voice configuration, set **A call comes in** to **Webhook**.
3. Use `https://your-domain/voice/incoming`, method `HTTP POST`.
4. Ensure the production `TWILIO_AUTH_TOKEN` is from the same Twilio project.
5. Call the number and complete at least these checks:
   - normal registration and final confirmation;
   - an invalid future DOB and short phone number;
   - a spoken correction such as "D-A-V-I-S, not D-A-V-I-E-S";
   - hang up midway, call back, and resume;
   - call again with the saved phone and choose update;
   - confirm the record in `/patients` and `/dashboard`.

## Security notes

- Secrets are environment variables and `.env` is ignored.
- Production Twilio requests can be cryptographically validated.
- The dashboard escapes values before inserting them into HTML.
- API inputs are validated; SQL uses bound parameters.
- The assessment asks for an openly testable API. For real data, add authentication/authorization, encryption controls, audit policy, retention/deletion policy, consent, and a HIPAA-eligible architecture before use.

## Known trade-offs and next steps

- Twilio `<Gather>` is turn-based, so it has short pauses between caller turns. Full-duplex interruption handling would use Twilio Media Streams or ConversationRelay with a realtime voice model.
- SQLite is appropriate for a single assessment instance, not horizontally scaled writes. Move to PostgreSQL before scaling.
- The LLM call is mocked in automated tests; a real OpenAI response must be smoke-tested with the candidate's key.
- No appointment scheduling or Spanish voice is included; these are bonus-only features.
- API authentication is intentionally omitted for evaluator access; do not store real patient data.

## Submission fields

Fill these only after verifying each item:

- Repository URL: `TBD`
- U.S. phone number: `TBD - not provisioned or verified`
- API base URL: `TBD - not deployed or verified`
- Dashboard URL: `TBD/dashboard`
- API docs URL: `TBD/docs`
- Reviewer credentials/notes: no API login by default; use synthetic data only

