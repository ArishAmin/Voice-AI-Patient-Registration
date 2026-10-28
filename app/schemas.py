import re
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


NAME_RE = re.compile(r"^[A-Za-z]+(?:[ '-][A-Za-z]+)*$")
PHONE_RE = re.compile(r"^\d{10}$")
ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
STATE_CODES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
Sex = Literal["Male", "Female", "Other", "Decline to Answer"]


def normalize_phone(value: str | None) -> str | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if not PHONE_RE.fullmatch(digits):
        raise ValueError("must be a valid U.S. 10-digit phone number")
    return digits


class PatientFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str = Field(min_length=1, max_length=50)
    last_name: str = Field(min_length=1, max_length=50)
    date_of_birth: date
    sex: Sex
    phone_number: str
    email: EmailStr | None = None
    address_line_1: str = Field(min_length=1, max_length=200)
    address_line_2: str | None = Field(default=None, max_length=100)
    city: str = Field(min_length=1, max_length=100)
    state: str
    zip_code: str
    insurance_provider: str | None = Field(default=None, max_length=100)
    insurance_member_id: str | None = Field(default=None, max_length=100)
    preferred_language: str = Field(default="English", min_length=1, max_length=50)
    emergency_contact_name: str | None = Field(default=None, max_length=100)
    emergency_contact_phone: str | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        if not NAME_RE.fullmatch(value):
            raise ValueError("may contain letters, spaces, hyphens, and apostrophes only")
        return value

    @field_validator("date_of_birth")
    @classmethod
    def valid_dob(cls, value: date) -> date:
        if value > date.today():
            raise ValueError("must not be in the future")
        return value

    @field_validator("phone_number", "emergency_contact_phone", mode="before")
    @classmethod
    def valid_phone(cls, value: str | None) -> str | None:
        return normalize_phone(value)

    @field_validator("state")
    @classmethod
    def valid_state(cls, value: str) -> str:
        value = value.upper()
        if value not in STATE_CODES:
            raise ValueError("must be a valid 2-letter U.S. state abbreviation")
        return value

    @field_validator("zip_code")
    @classmethod
    def valid_zip(cls, value: str) -> str:
        if not ZIP_RE.fullmatch(value):
            raise ValueError("must be a 5-digit ZIP or ZIP+4")
        return value


class PatientCreate(PatientFields):
    pass


class PatientUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    first_name: str | None = Field(default=None, min_length=1, max_length=50)
    last_name: str | None = Field(default=None, min_length=1, max_length=50)
    date_of_birth: date | None = None
    sex: Sex | None = None
    phone_number: str | None = None
    email: EmailStr | None = None
    address_line_1: str | None = Field(default=None, min_length=1, max_length=200)
    address_line_2: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, min_length=1, max_length=100)
    state: str | None = None
    zip_code: str | None = None
    insurance_provider: str | None = Field(default=None, max_length=100)
    insurance_member_id: str | None = Field(default=None, max_length=100)
    preferred_language: str | None = Field(default=None, min_length=1, max_length=50)
    emergency_contact_name: str | None = Field(default=None, max_length=100)
    emergency_contact_phone: str | None = None

    @model_validator(mode="after")
    def required_fields_cannot_be_null(self):
        required = {
            "first_name", "last_name", "date_of_birth", "sex", "phone_number",
            "address_line_1", "city", "state", "zip_code",
        }
        null_required = sorted(required & self.model_fields_set - {name for name in required if getattr(self, name) is not None})
        if null_required:
            raise ValueError(f"required fields cannot be null: {', '.join(null_required)}")
        return self

    @field_validator("first_name", "last_name")
    @classmethod
    def valid_name(cls, value: str | None) -> str | None:
        if value is not None and not NAME_RE.fullmatch(value):
            raise ValueError("may contain letters, spaces, hyphens, and apostrophes only")
        return value

    @field_validator("date_of_birth")
    @classmethod
    def valid_dob(cls, value: date | None) -> date | None:
        if value is not None and value > date.today():
            raise ValueError("must not be in the future")
        return value

    @field_validator("phone_number", "emergency_contact_phone", mode="before")
    @classmethod
    def valid_phone(cls, value: str | None) -> str | None:
        return normalize_phone(value)

    @field_validator("state")
    @classmethod
    def valid_state(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.upper()
        if value not in STATE_CODES:
            raise ValueError("must be a valid 2-letter U.S. state abbreviation")
        return value

    @field_validator("zip_code")
    @classmethod
    def valid_zip(cls, value: str | None) -> str | None:
        if value is not None and not ZIP_RE.fullmatch(value):
            raise ValueError("must be a 5-digit ZIP or ZIP+4")
        return value


class PatientRead(PatientFields):
    patient_id: UUID
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class Envelope(BaseModel):
    data: object | None = None
    error: object | None = None

