"""Tests for M0 (health endpoint) and M1 (models and schemas persistence)."""

from datetime import date, datetime, timezone
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.models import Job, Certificate, JobStatus, CertificateStatus
from app.schemas import JobCreate, RecipientIn


def test_health_check(client: TestClient) -> None:
    """Verify GET /health returns status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_insert_and_read_back_job_with_certificates(db_session: Session) -> None:
    """Verify inserting and reading back a Job with associated Certificates."""
    # Create a job
    job = Job(
        title="Python Specialist Certification",
        course_name="Backend Engineering Masterclass",
        issuer_name="Aereo Academy",
        issue_date=date(2026, 10, 8),
        total_count=2,
        idempotency_key="idemp-key-12345",
        status=JobStatus.PENDING,
    )
    db_session.add(job)
    db_session.flush()

    assert job.id is not None
    assert job.created_at is not None

    # Add certificates
    cert1 = Certificate(
        job_id=job.id,
        row_index=0,
        recipient_name="Alice Smith",
        recipient_email="alice@example.com",
        raw_input={"name": "Alice Smith", "email": "alice@example.com"},
        status=CertificateStatus.PENDING,
    )
    cert2 = Certificate(
        job_id=job.id,
        row_index=1,
        recipient_name="Bob Jones",
        recipient_email="bob@example.com",
        raw_input={"name": "Bob Jones", "email": "bob@example.com"},
        status=CertificateStatus.PENDING,
    )
    db_session.add_all([cert1, cert2])
    db_session.commit()

    # Read back from database
    fetched_job = db_session.query(Job).filter(Job.id == job.id).first()
    assert fetched_job is not None
    assert fetched_job.title == "Python Specialist Certification"
    assert fetched_job.course_name == "Backend Engineering Masterclass"
    assert fetched_job.issuer_name == "Aereo Academy"
    assert fetched_job.total_count == 2
    assert fetched_job.status == JobStatus.PENDING
    assert fetched_job.idempotency_key == "idemp-key-12345"
    assert len(fetched_job.certificates) == 2

    # Verify certificate items
    c1, c2 = fetched_job.certificates[0], fetched_job.certificates[1]
    assert c1.row_index == 0
    assert c1.recipient_name == "Alice Smith"
    assert c1.recipient_email == "alice@example.com"
    assert c1.raw_input == {"name": "Alice Smith", "email": "alice@example.com"}
    assert c1.status == CertificateStatus.PENDING

    assert c2.row_index == 1
    assert c2.recipient_name == "Bob Jones"
    assert c2.recipient_email == "bob@example.com"


def test_recipient_schema_validation() -> None:
    """Verify strict RecipientIn validation rules."""
    # Valid recipient
    recipient = RecipientIn(name="  Jane Doe  ", email="Jane.Doe@Example.COM")
    assert recipient.name == "Jane Doe"
    assert recipient.email == "jane.doe@example.com"

    # Blank name
    with pytest.raises(ValueError):
        RecipientIn(name="   ", email="jane@example.com")

    # Invalid email
    with pytest.raises(ValueError):
        RecipientIn(name="Jane Doe", email="not-an-email")

    # Control characters in name
    with pytest.raises(ValueError):
        RecipientIn(name="Jane\x00Doe", email="jane@example.com")
