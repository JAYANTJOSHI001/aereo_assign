"""Tests for Level-1 request validation and Level-2 recipient validation."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Certificate, CertificateStatus, Job


def test_request_validation_empty_recipients_422(client: TestClient) -> None:
    """Verify empty recipients list is rejected at request schema validation with 422."""
    payload = {
        "title": "Validation Test",
        "course_name": "Course A",
        "issuer_name": "Issuer A",
        "recipients": [],
    }
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "VALIDATION_ERROR"


def test_request_validation_oversized_recipients_422(client: TestClient, monkeypatch) -> None:
    """Verify recipients list exceeding MAX_RECIPIENTS is rejected with 422."""
    monkeypatch.setattr(settings, "MAX_RECIPIENTS", 3)
    payload = {
        "title": "Validation Test",
        "course_name": "Course A",
        "issuer_name": "Issuer A",
        "recipients": [
            {"name": f"User {i}", "email": f"user{i}@example.com"}
            for i in range(4)
        ],
    }
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "VALIDATION_ERROR"


def test_request_validation_missing_or_blank_fields_422(client: TestClient) -> None:
    """Verify missing or blank required fields are rejected with 422."""
    # Blank title
    payload = {
        "title": "   ",
        "course_name": "Course A",
        "issuer_name": "Issuer A",
        "recipients": [{"name": "Bob", "email": "bob@example.com"}],
    }
    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 422

    # Missing course_name
    payload_missing = {
        "title": "Certificate Title",
        "issuer_name": "Issuer A",
        "recipients": [{"name": "Bob", "email": "bob@example.com"}],
    }
    response = client.post("/api/v1/jobs", json=payload_missing)
    assert response.status_code == 422


def test_recipient_validation_invalid_and_duplicate_rows_handled_individually(
    client: TestClient,
    db_session: Session,
) -> None:
    """
    Verify Level-2 recipient validation:
    - Invalid email row is marked failed with VALIDATION_ERROR.
    - Blank name row is marked failed with VALIDATION_ERROR.
    - Duplicate email within same job: first row succeeds (pending), second fails with DUPLICATE_RECIPIENT.
    - Valid row with extra unknown field succeeds (pending).
    - Entire job is still accepted with 202.
    """
    payload = {
        "title": "Safety & Quality Verification",
        "course_name": "Fullstack Engineering",
        "issuer_name": "Aereo Tech",
        "recipients": [
            {"name": "Valid First", "email": "repeat@example.com", "extra_field": "ignored"},
            {"name": "Valid Second", "email": "repeat@example.com"},  # duplicate email
            {"name": "Bad Email User", "email": "not-an-email"},       # invalid email
            {"name": "   ", "email": "blankname@example.com"},          # blank name
            {"name": "Control\x00Char", "email": "control@example.com"},# control character
            {"name": "Valid Third", "email": "third@example.com"},     # valid
        ],
    }

    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    certs = (
        db_session.query(Certificate)
        .filter(Certificate.job_id == job_id)
        .order_by(Certificate.row_index)
        .all()
    )
    assert len(certs) == 6

    # Row 0: Valid first occurrence (processed to SUCCESS)
    assert certs[0].row_index == 0
    assert certs[0].status == CertificateStatus.SUCCESS
    assert certs[0].error_code is None

    # Row 1: Duplicate email -> DUPLICATE_RECIPIENT
    assert certs[1].row_index == 1
    assert certs[1].status == CertificateStatus.FAILED
    assert certs[1].error_code == "DUPLICATE_RECIPIENT"
    assert "Duplicate recipient email" in certs[1].error_message

    # Row 2: Invalid email -> VALIDATION_ERROR
    assert certs[2].row_index == 2
    assert certs[2].status == CertificateStatus.FAILED
    assert certs[2].error_code == "VALIDATION_ERROR"
    assert "email" in certs[2].error_message

    # Row 3: Blank name -> VALIDATION_ERROR
    assert certs[3].row_index == 3
    assert certs[3].status == CertificateStatus.FAILED
    assert certs[3].error_code == "VALIDATION_ERROR"
    assert "name" in certs[3].error_message

    # Row 4: Control character in name -> VALIDATION_ERROR
    assert certs[4].row_index == 4
    assert certs[4].status == CertificateStatus.FAILED
    assert certs[4].error_code == "VALIDATION_ERROR"

    # Row 5: Valid third (processed to SUCCESS)
    assert certs[5].row_index == 5
    assert certs[5].status == CertificateStatus.SUCCESS
    assert certs[5].error_code is None
