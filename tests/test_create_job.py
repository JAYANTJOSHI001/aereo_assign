"""Tests for POST /api/v1/jobs endpoint and job creation behavior."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Certificate, Job, JobStatus


def test_create_job_success_202(client: TestClient, db_session: Session) -> None:
    """Verify successful job submission returns 202 with Location header and persists rows."""
    payload = {
        "title": "FastAPI Certification",
        "course_name": "Modern Backend Architecture",
        "issuer_name": "Aereo Learning",
        "issue_date": "2026-10-08",
        "recipients": [
            {"name": "Alice Baker", "email": "alice@example.com"},
            {"name": "Charlie Davis", "email": "charlie@example.com"},
        ],
    }

    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 202

    data = response.json()
    job_id = data["job_id"]
    assert job_id is not None
    assert data["status"] == "pending"
    assert data["total"] == 2
    assert data["status_url"] == f"/api/v1/jobs/{job_id}"
    assert response.headers["Location"] == f"/api/v1/jobs/{job_id}"

    # Check persistence in DB
    db_job = db_session.query(Job).filter(Job.id == job_id).first()
    assert db_job is not None
    assert db_job.total_count == 2
    assert db_job.title == "FastAPI Certification"

    certs = db_session.query(Certificate).filter(Certificate.job_id == job_id).all()
    assert len(certs) == 2
    assert {c.recipient_name for c in certs} == {"Alice Baker", "Charlie Davis"}


def test_create_job_idempotency_replay_200(client: TestClient, db_session: Session) -> None:
    """Verify sending same Idempotency-Key returns 200 with original job and no duplicate rows."""
    headers = {"Idempotency-Key": "test-idempotency-key-abc"}
    payload = {
        "title": "Idempotency Test",
        "course_name": "Distributed Systems",
        "issuer_name": "Aereo Academy",
        "recipients": [
            {"name": "User One", "email": "one@example.com"},
        ],
    }

    # First request -> 202 Accepted
    resp1 = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert resp1.status_code == 202
    job_id_1 = resp1.json()["job_id"]

    # Second request with identical key -> 200 OK
    resp2 = client.post("/api/v1/jobs", json=payload, headers=headers)
    assert resp2.status_code == 200
    job_id_2 = resp2.json()["job_id"]
    assert job_id_1 == job_id_2

    # Assert no duplicate jobs or certificates created in DB
    job_count = db_session.query(Job).filter(Job.idempotency_key == "test-idempotency-key-abc").count()
    assert job_count == 1

    cert_count = db_session.query(Certificate).filter(Certificate.job_id == job_id_1).count()
    assert cert_count == 1
