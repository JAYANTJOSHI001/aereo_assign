"""Tests for job status tracking, derived progress, failures list, and certificate listing."""

import uuid
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import pytest

from app.config import settings
from app import workers
from app.models import Certificate, CertificateStatus, Job, JobStatus


def test_job_status_completed(client: TestClient) -> None:
    """Verify job reaching 'completed' status when all certificates succeed."""
    payload = {
        "title": "Machine Learning Specialization",
        "course_name": "Deep Learning Fundamentals",
        "issuer_name": "Aereo AI Lab",
        "recipients": [
            {"name": "Dev One", "email": "dev1@example.com"},
            {"name": "Dev Two", "email": "dev2@example.com"},
        ],
    }

    create_resp = client.post("/api/v1/jobs", json=payload)
    assert create_resp.status_code == 202
    job_id = create_resp.json()["job_id"]

    status_resp = client.get(f"/api/v1/jobs/{job_id}")
    assert status_resp.status_code == 200
    data = status_resp.json()

    assert data["job_id"] == job_id
    assert data["status"] == "completed"
    assert data["progress"]["total"] == 2
    assert data["progress"]["succeeded"] == 2
    assert data["progress"]["failed"] == 0
    assert data["progress"]["pending"] == 0
    assert data["progress"]["percent_complete"] == 100.0
    assert data["failures"] == []
    assert data["failures_truncated"] is False
    assert data["started_at"] is not None
    assert data["finished_at"] is not None


def test_job_status_completed_with_errors(client: TestClient) -> None:
    """Verify job reaching 'completed_with_errors' when some recipients fail."""
    payload = {
        "title": "Cloud Architect Program",
        "course_name": "Terraform & AWS",
        "issuer_name": "Aereo Cloud",
        "recipients": [
            {"name": "Valid Student", "email": "valid@example.com"},
            {"name": "Bad Email", "email": "invalid-email"},  # validation error
        ],
    }

    create_resp = client.post("/api/v1/jobs", json=payload)
    assert create_resp.status_code == 202
    job_id = create_resp.json()["job_id"]

    status_resp = client.get(f"/api/v1/jobs/{job_id}")
    assert status_resp.status_code == 200
    data = status_resp.json()

    assert data["status"] == "completed_with_errors"
    assert data["progress"]["total"] == 2
    assert data["progress"]["succeeded"] == 1
    assert data["progress"]["failed"] == 1
    assert data["progress"]["pending"] == 0
    assert data["progress"]["percent_complete"] == 100.0

    assert len(data["failures"]) == 1
    failure = data["failures"][0]
    assert failure["row_index"] == 1
    assert failure["error_code"] == "VALIDATION_ERROR"
    assert "email" in failure["error_message"]


def test_job_status_failed_all_invalid(client: TestClient) -> None:
    """Verify job reaching 'failed' when all recipients fail."""
    payload = {
        "title": "Security Training",
        "course_name": "Incident Response",
        "issuer_name": "Aereo Cyber",
        "recipients": [
            {"name": "Bad Email 1", "email": "invalid1"},
            {"name": "Bad Email 2", "email": "invalid2"},
        ],
    }

    create_resp = client.post("/api/v1/jobs", json=payload)
    assert create_resp.status_code == 202
    job_id = create_resp.json()["job_id"]

    status_resp = client.get(f"/api/v1/jobs/{job_id}")
    assert status_resp.status_code == 200
    data = status_resp.json()

    assert data["status"] == "failed"
    assert data["progress"]["total"] == 2
    assert data["progress"]["succeeded"] == 0
    assert data["progress"]["failed"] == 2
    assert data["progress"]["pending"] == 0
    assert data["progress"]["percent_complete"] == 100.0
    assert len(data["failures"]) == 2


def test_job_status_pending_with_worker_disabled(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify pending job state and 0% progress when background execution is deferred."""
    # Prevent worker execution
    monkeypatch.setattr(workers, "submit", lambda job_id: None)

    payload = {
        "title": "Pending Verification",
        "course_name": "Async Architectures",
        "issuer_name": "Aereo Lab",
        "recipients": [
            {"name": "Pending Recipient", "email": "pending@example.com"},
        ],
    }

    create_resp = client.post("/api/v1/jobs", json=payload)
    job_id = create_resp.json()["job_id"]

    status_resp = client.get(f"/api/v1/jobs/{job_id}")
    assert status_resp.status_code == 200
    data = status_resp.json()

    assert data["status"] == "pending"
    assert data["progress"]["total"] == 1
    assert data["progress"]["succeeded"] == 0
    assert data["progress"]["failed"] == 0
    assert data["progress"]["pending"] == 1
    assert data["progress"]["percent_complete"] == 0.0


def test_job_status_unknown_id_404(client: TestClient) -> None:
    """Verify unknown job ID returns 404 with structured error."""
    response = client.get(f"/api/v1/jobs/{uuid.uuid4()}")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "NOT_FOUND"


def test_job_status_failures_capped_at_100(client: TestClient, db_session: Session) -> None:
    """Verify failures list is capped at 100 with failures_truncated set to True."""
    # Directly insert a job with 105 failed certificates
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        title="Mass Failure Test",
        course_name="Stress Testing",
        issuer_name="Aereo QA",
        total_count=105,
        status=JobStatus.FAILED,
    )
    db_session.add(job)

    certs = [
        Certificate(
            id=str(uuid.uuid4()),
            job_id=job_id,
            row_index=i,
            recipient_name=f"Failed {i}",
            recipient_email=f"failed{i}@example.com",
            raw_input={"name": f"Failed {i}"},
            status=CertificateStatus.FAILED,
            error_code="VALIDATION_ERROR",
            error_message="Test failure",
        )
        for i in range(105)
    ]
    db_session.add_all(certs)
    db_session.commit()

    resp = client.get(f"/api/v1/jobs/{job_id}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["progress"]["failed"] == 105
    assert len(data["failures"]) == 100
    assert data["failures_truncated"] is True


def test_list_job_certificates_pagination_and_filtering(client: TestClient) -> None:
    """Verify GET /api/v1/jobs/{id}/certificates supports filtering and pagination."""
    payload = {
        "title": "Certificates Listing Test",
        "course_name": "API Design",
        "issuer_name": "Aereo Academy",
        "recipients": [
            {"name": "Valid 1", "email": "valid1@example.com"},
            {"name": "Valid 2", "email": "valid2@example.com"},
            {"name": "Valid 3", "email": "valid3@example.com"},
            {"name": "Bad Email", "email": "not-valid-email"},
        ],
    }

    create_resp = client.post("/api/v1/jobs", json=payload)
    job_id = create_resp.json()["job_id"]

    # 1. List all without filter
    all_resp = client.get(f"/api/v1/jobs/{job_id}/certificates")
    assert all_resp.status_code == 200
    all_data = all_resp.json()
    assert all_data["total"] == 4
    assert len(all_data["items"]) == 4

    # Confirm download_url present for success, None for failed
    for item in all_data["items"]:
        if item["status"] == "success":
            assert item["download_url"] is not None
            assert item["download_url"].startswith(f"/api/v1/certificates/{item['certificate_id']}/download")
        else:
            assert item["download_url"] is None
        # Confirm file_path is NOT exposed in API response
        assert "file_path" not in item

    # 2. Filter by status=success
    success_resp = client.get(f"/api/v1/jobs/{job_id}/certificates?status=success")
    assert success_resp.status_code == 200
    success_data = success_resp.json()
    assert success_data["total"] == 3
    assert len(success_data["items"]) == 3
    assert all(item["status"] == "success" for item in success_data["items"])

    # 3. Filter by status=failed
    failed_resp = client.get(f"/api/v1/jobs/{job_id}/certificates?status=failed")
    assert failed_resp.status_code == 200
    failed_data = failed_resp.json()
    assert failed_data["total"] == 1
    assert len(failed_data["items"]) == 1
    assert failed_data["items"][0]["status"] == "failed"

    # 4. Pagination (limit=2, offset=1)
    page_resp = client.get(f"/api/v1/jobs/{job_id}/certificates?limit=2&offset=1")
    assert page_resp.status_code == 200
    page_data = page_resp.json()
    assert page_data["total"] == 4
    assert page_data["limit"] == 2
    assert page_data["offset"] == 1
    assert len(page_data["items"]) == 2
    assert page_data["items"][0]["row_index"] == 1
    assert page_data["items"][1]["row_index"] == 2
