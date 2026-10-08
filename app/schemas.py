"""Pydantic schemas for request validation and response serialization."""

import re
from datetime import date, datetime, timezone
from typing import Any
from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)

from app.models import CertificateStatus, JobStatus

# Regex to detect control characters (excluding standard whitespace if needed, but control chars 0x00-0x1F, 0x7F are forbidden)
CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")


class RecipientIn(BaseModel):
    """Strict recipient validation model evaluated per-row."""

    name: str = Field(..., min_length=1, max_length=100, description="Recipient full name")
    email: EmailStr = Field(..., max_length=255, description="Recipient email address")

    model_config = ConfigDict(extra="ignore")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Trim whitespace and ensure no control characters exist."""
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Name cannot be empty or whitespace only")
        if len(trimmed) > 100:
            raise ValueError("Name cannot exceed 100 characters")
        if CONTROL_CHAR_RE.search(trimmed):
            raise ValueError("Name contains invalid control characters")
        return trimmed

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        """Normalize email address to lowercase."""
        return v.lower().strip()


class JobCreate(BaseModel):
    """Bulk job creation request containing loose recipient payloads."""

    title: str = Field(..., min_length=1, max_length=255, description="Certificate title")
    course_name: str = Field(..., min_length=1, max_length=255, description="Course or event name")
    issuer_name: str = Field(..., min_length=1, max_length=255, description="Issuing organization")
    issue_date: date | None = Field(default=None, description="Issue date (defaults to today)")
    recipients: list[dict[str, Any]] = Field(
        ...,
        description="List of raw recipient objects to validate individually",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("title", "course_name", "issuer_name")
    @classmethod
    def validate_non_empty(cls, v: str) -> str:
        """Ensure title, course name, and issuer name are not blank."""
        trimmed = v.strip()
        if not trimmed:
            raise ValueError("Field cannot be empty or whitespace only")
        return trimmed

    @field_validator("recipients")
    @classmethod
    def validate_recipients_list(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ensure recipients list is not empty and does not exceed MAX_RECIPIENTS."""
        if not v:
            raise ValueError("Recipients list cannot be empty")
        from app.config import settings
        if len(v) > settings.MAX_RECIPIENTS:
            raise ValueError(f"Recipients list exceeds maximum limit of {settings.MAX_RECIPIENTS}")
        return v


class JobAcceptedResponse(BaseModel):
    """HTTP 202 Accepted response upon job creation."""

    job_id: str
    status: JobStatus
    total: int
    status_url: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProgressResponse(BaseModel):
    """Progress stats derived from certificate statuses."""

    total: int
    succeeded: int
    failed: int
    pending: int
    percent_complete: float


class FailureDetail(BaseModel):
    """Summary of a failed recipient certificate generation."""

    row_index: int
    recipient_name: str | None = None
    recipient_email: str | None = None
    error_code: str
    error_message: str


class JobDetailResponse(BaseModel):
    """Detailed job status response including derived progress and recent failures."""

    job_id: str
    status: JobStatus
    title: str
    course_name: str
    issuer_name: str
    issue_date: date
    progress: ProgressResponse
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failures: list[FailureDetail] = Field(default_factory=list)
    failures_truncated: bool = False

    model_config = ConfigDict(from_attributes=True)


class CertificateItemResponse(BaseModel):
    """Certificate detail returned in certificate listings."""

    certificate_id: str
    row_index: int
    recipient_name: str | None = None
    recipient_email: str | None = None
    status: CertificateStatus
    certificate_number: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    download_url: str | None = None
    generated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class CertificateListResponse(BaseModel):
    """Paginated list of certificates for a job."""

    total: int
    limit: int
    offset: int
    items: list[CertificateItemResponse]


class ErrorDetail(BaseModel):
    """Standardized error structure."""

    code: str
    message: str
    details: list[dict[str, Any]] | None = None


class ErrorResponse(BaseModel):
    """Uniform error response wrapper."""

    error: ErrorDetail
