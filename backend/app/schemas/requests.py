"""Validated request bodies."""
from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.application import LOAN_TYPES

ROLE = Literal["admin", "analyst", "reviewer"]
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
CIN_RE = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$")


def _contains_nul(v) -> bool:
    if isinstance(v, str):
        return "\x00" in v
    if isinstance(v, dict):
        return any(_contains_nul(k) or _contains_nul(x) for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return any(_contains_nul(x) for x in v)
    return False


class Clean(BaseModel):
    """Base of every request body: unknown fields, NUL characters (PostgreSQL cannot store them) and non-finite
    numbers (JSON parsing accepts Infinity/NaN literals) are rejected with a 422 instead of failing at the database."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @model_validator(mode="before")
    @classmethod
    def _reject_nul(cls, data):
        if _contains_nul(data):
            raise ValueError("Text must not contain NUL (\\x00) characters.")
        return data


class Strict(Clean):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


def _password(v: str) -> str:
    if len(v.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes.")
    if len(v) < 10 or not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
        raise ValueError("Password must be at least 10 characters and include letters and numbers.")
    return v


class LoginIn(Strict):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RegisterIn(Strict):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str
    organization_name: str = Field(min_length=2, max_length=200)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _password(v)


class OrganizationIn(Strict):
    name: str = Field(min_length=2, max_length=200)


class UserCreateIn(Strict):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)
    password: str
    role: ROLE = "analyst"
    title: str | None = Field(default=None, max_length=120)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _password(v)


class UserUpdateIn(Strict):
    role: ROLE | None = None
    is_active: bool | None = None
    full_name: str | None = Field(default=None, min_length=2, max_length=200)
    title: str | None = Field(default=None, max_length=120)


class DirectorIn(Strict):
    name: str = Field(min_length=2, max_length=200)
    din: str | None = Field(default=None, pattern=r"^\d{8}$")
    designation: str | None = Field(default=None, max_length=120)


class PromoterIn(Strict):
    name: str = Field(min_length=2, max_length=200)
    shareholding_pct: float | None = Field(default=None, ge=0, le=100)
    experience_years: int | None = Field(default=None, ge=0, le=80)
    background: str | None = Field(default=None, max_length=2000)


class CompanyIn(Strict):
    name: str = Field(min_length=2, max_length=255)
    industry: str = Field(min_length=2, max_length=120)
    business_type: str = Field(min_length=2, max_length=80)
    cin: str | None = None
    pan: str | None = None
    gstin: str | None = None
    incorporation_date: date | None = None
    registered_address: str | None = Field(default=None, max_length=1000)
    headquarters: str | None = Field(default=None, max_length=200)
    annual_turnover: float | None = Field(default=None, ge=0)
    website: str | None = Field(default=None, max_length=255)
    contact_person: str | None = Field(default=None, max_length=200)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(default=None, max_length=40)
    business_description: str | None = Field(default=None, max_length=5000)
    subsidiaries: str | None = Field(default=None, max_length=2000)
    directors: list[DirectorIn] = Field(default_factory=list, max_length=30)
    promoters: list[PromoterIn] = Field(default_factory=list, max_length=30)

    @field_validator("cin", "pan", "gstin", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() or None if isinstance(v, str) else v

    @field_validator("pan")
    @classmethod
    def _pan(cls, v):
        if v and not PAN_RE.match(v):
            raise ValueError("PAN must look like ABCDE1234F.")
        return v

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, v):
        if v and not GSTIN_RE.match(v):
            raise ValueError("GSTIN must be a valid 15-character GSTIN.")
        return v

    @field_validator("cin")
    @classmethod
    def _cin(cls, v):
        if v and not CIN_RE.match(v):
            raise ValueError("CIN must be a valid 21-character Corporate Identification Number.")
        return v

    @field_validator("incorporation_date")
    @classmethod
    def _past(cls, v):
        if v and v > date.today():
            raise ValueError("Incorporation date cannot be in the future.")
        return v


class CompanyUpdateIn(CompanyIn):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    industry: str | None = Field(default=None, min_length=2, max_length=120)
    business_type: str | None = Field(default=None, min_length=2, max_length=80)
    directors: list[DirectorIn] | None = None
    promoters: list[PromoterIn] | None = None


class BorrowingIn(Clean):
    lender: str = Field(min_length=2, max_length=200)
    facility_type: str = Field(default="term_loan", max_length=40)
    sanctioned_amount: float | None = Field(default=None, ge=0)
    outstanding_amount: float | None = Field(default=None, ge=0)
    interest_rate: float | None = Field(default=None, ge=0, le=40)
    remaining_tenure_months: int | None = Field(default=None, ge=0, le=480)
    annual_debt_service: float | None = Field(default=None, ge=0)


class CollateralIn(Clean):
    type: Literal["property", "plant_machinery", "fixed_deposit", "receivables", "inventory", "shares", "vehicle", "other"]
    description: str | None = Field(default=None, max_length=500)
    value: float = Field(ge=0)
    valuation_date: date | None = None
    existing_charge: bool = False


class GuarantorIn(Clean):
    name: str = Field(min_length=2, max_length=200)
    relationship: str | None = Field(default=None, max_length=120)
    net_worth: float | None = Field(default=None, ge=0)


class ApplicationIn(Strict):
    company_id: int
    loan_type: str
    requested_amount: float = Field(gt=0, le=1e11)
    tenure_months: int = Field(ge=1, le=360)
    purpose: str = Field(min_length=5, max_length=2000)
    repayment_frequency: Literal["monthly", "quarterly", "half_yearly", "bullet"] = "monthly"
    moratorium_months: int = Field(default=0, ge=0, le=60)
    application_date: date | None = None
    existing_borrowings: list[BorrowingIn] = Field(default_factory=list, max_length=30)
    collateral: list[CollateralIn] = Field(default_factory=list, max_length=30)
    guarantors: list[GuarantorIn] = Field(default_factory=list, max_length=10)

    @field_validator("loan_type")
    @classmethod
    def _lt(cls, v):
        if v not in LOAN_TYPES:
            raise ValueError(f"loan_type must be one of: {', '.join(LOAN_TYPES)}")
        return v


class ApplicationUpdateIn(Strict):
    loan_type: str | None = None
    requested_amount: float | None = Field(default=None, gt=0, le=1e11)
    tenure_months: int | None = Field(default=None, ge=1, le=360)
    purpose: str | None = Field(default=None, min_length=5, max_length=2000)
    repayment_frequency: Literal["monthly", "quarterly", "half_yearly", "bullet"] | None = None
    moratorium_months: int | None = Field(default=None, ge=0, le=60)
    existing_borrowings: list[BorrowingIn] | None = None
    collateral: list[CollateralIn] | None = None
    guarantors: list[GuarantorIn] | None = None
    current_step: int | None = Field(default=None, ge=1, le=9)
    assigned_reviewer_id: int | None = None

    @field_validator("loan_type")
    @classmethod
    def _lt(cls, v):
        if v is not None and v not in LOAN_TYPES:
            raise ValueError(f"loan_type must be one of: {', '.join(LOAN_TYPES)}")
        return v


class MetricCorrectionIn(Strict):
    verification_status: Literal["verified", "corrected", "rejected", "unverified"]
    corrected_value: float | None = None
    correction_note: str | None = Field(default=None, max_length=1000)


class ReconReviewIn(Strict):
    review_status: Literal["open", "under_review", "explained", "escalated"]
    comment: str | None = Field(default=None, max_length=4000)


class ObservationIn(Strict):
    category: Literal["factory_inspection", "management_assessment", "business_environment"]
    sub_category: str = Field(min_length=2, max_length=60, pattern=r"^[a-z_]+$")
    title: str = Field(min_length=3, max_length=255)
    notes: str = Field(min_length=3, max_length=8000)
    rating: int | None = Field(default=None, ge=1, le=5)
    numeric_value: float | None = None
    observed_on: date | None = None
    severity_override: Literal["info", "low", "medium", "high", "critical"] | None = None


class ObservationReviewIn(Strict):
    reviewer_status: Literal["pending", "verified", "disputed"]
    reviewer_comment: str | None = Field(default=None, max_length=2000)


class FindingReviewIn(Strict):
    verification_status: Literal["unverified", "verified", "disputed", "not_relevant"] | None = None
    include_in_assessment: bool | None = None
    reviewer_note: str | None = Field(default=None, max_length=2000)


class ChatIn(Strict):
    question: str = Field(min_length=3, max_length=1000)
    session_id: int | None = None


class CommentIn(Strict):
    body: str = Field(min_length=1, max_length=8000)
    section: Literal["general", "financials", "reconciliation", "risk", "recommendation", "documents", "due_diligence"] = "general"
    entity_type: Literal["reconciliation", "metric", "document", "recommendation", "observation"] | None = None
    entity_id: int | None = None


class DecisionIn(Strict):
    decision: Literal["approved", "rejected", "returned"]
    comments: str = Field(min_length=10, max_length=8000)
    final_amount: float | None = Field(default=None, ge=0)
    final_rate: float | None = Field(default=None, ge=0, le=40)
    final_tenure_months: int | None = Field(default=None, ge=1, le=360)


class PolicyIn(Strict):
    name: str = Field(min_length=3, max_length=200)
    config: dict
    change_note: str = Field(min_length=5, max_length=2000)
