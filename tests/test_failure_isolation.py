"""Tests for per-certificate failure isolation and startup job recovery."""

from datetime import date, datetime, timezone
from pathlib import Path
import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.rendering import renderer
from app.services.job_service import recover_incomplete_jobs
from app.storage import storage


def test_failure_isolation_one_bad_row_does_not_block_others(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify that an unhandled exception in one certificate render:
    - Fails only that certificate with RENDER_ERROR.
    - Does not block preceding or succeeding certificates.
    - Sets final job status to 'completed_with_errors'.
    """
    real_render = renderer.render_certificate

    def mock_render(data: renderer.CertificateData) -> bytes:
        if data.recipient_name == "Faulty Recipient":
            raise RuntimeError("Injected render engine failure")
        return real_render(data)

    monkeypatch.setattr(renderer, "render_certificate", mock_render)

    payload = {
        "title": "Data Engineering Diploma",
        "course_name": "Distributed Stream Processing",
        "issuer_name": "Aereo University",
        "issue_date": "2026-10-08",
        "recipients": [
            {"name": "First Recipient", "email": "first@example.com"},
            {"name": "Faulty Recipient", "email": "faulty@example.com"},
            {"name": "Third Recipient", "email": "third@example.com"},
        ],
    }

    response = client.post("/api/v1/jobs", json=payload)
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    db_session.expire_all()
    db_job = db_session.query(Job).filter(Job.id == job_id).first()
    assert db_job is not None
    assert db_job.status == JobStatus.COMPLETED_WITH_ERRORS
    assert db_job.started_at is not None
    assert db_job.finished_at is not None

    certs = (
        db_session.query(Certificate)
        .filter(Certificate.job_id == job_id)
        .order_by(Certificate.row_index)
        .all()
    )
    assert len(certs) == 3

    # Row 0: Succeeded
    assert certs[0].row_index == 0
    assert certs[0].status == CertificateStatus.SUCCESS
    assert certs[0].error_code is None
    assert certs[0].certificate_number is not None
    assert storage.exists(certs[0].file_path) is True

    # Row 1: Failed with RENDER_ERROR
    assert certs[1].row_index == 1
    assert certs[1].status == CertificateStatus.FAILED
    assert certs[1].error_code == "RENDER_ERROR"
    assert "Injected render engine failure" in certs[1].error_message
    assert certs[1].file_path is None

    # Row 2: Succeeded (Later row successfully generated!)
    assert certs[2].row_index == 2
    assert certs[2].status == CertificateStatus.SUCCESS
    assert certs[2].error_code is None
    assert certs[2].certificate_number is not None
    assert storage.exists(certs[2].file_path) is True


def test_recovery_does_not_regenerate_already_successful_certificates(
    db_session: Session,
    tmp_storage: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify recover_incomplete_jobs:
    - Finds a job stuck in 'processing' with mixed success/pending certificates.
    - Transitions job to pending and executes remaining pending certificates.
    - Does NOT regenerate already successful certificates.
    - Reaches completed terminal status.
    """
    from app.config import settings

    monkeypatch.setattr(settings, "EAGER", True)

    fixed_old_time = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        status=JobStatus.PROCESSING,
        title="Recovery Test Certification",
        course_name="Fault Tolerance in Python",
        issuer_name="Aereo Institute",
        issue_date=date(2026, 10, 8),
        total_count=3,
        created_at=fixed_old_time,
        started_at=fixed_old_time,
    )
    db_session.add(job)

    # 1 already successful cert
    cert1_id = str(uuid.uuid4())
    cert1 = Certificate(
        id=cert1_id,
        job_id=job.id,
        row_index=0,
        recipient_name="Already Finished",
        recipient_email="finished@example.com",
        raw_input={"name": "Already Finished", "email": "finished@example.com"},
        status=CertificateStatus.SUCCESS,
        certificate_number="CERT-ORIGINAL-NUMBER",
        file_path=f"{job_id}/{cert1_id}.pdf",
        generated_at=fixed_old_time,
    )

    # 2 pending certs
    cert2_id = str(uuid.uuid4())
    cert2 = Certificate(
        id=cert2_id,
        job_id=job.id,
        row_index=1,
        recipient_name="Pending Recipient A",
        recipient_email="pending_a@example.com",
        raw_input={"name": "Pending Recipient A", "email": "pending_a@example.com"},
        status=CertificateStatus.PENDING,
    )
    cert3_id = str(uuid.uuid4())
    cert3 = Certificate(
        id=cert3_id,
        job_id=job.id,
        row_index=2,
        recipient_name="Pending Recipient B",
        recipient_email="pending_b@example.com",
        raw_input={"name": "Pending Recipient B", "email": "pending_b@example.com"},
        status=CertificateStatus.PENDING,
    )

    db_session.add_all([cert1, cert2, cert3])
    db_session.commit()

    # Create dummy file on disk for cert1 so storage is consistent
    dummy_cert_dir = tmp_storage / job_id
    dummy_cert_dir.mkdir(parents=True, exist_ok=True)
    (dummy_cert_dir / f"{cert1_id}.pdf").write_bytes(b"%PDF-old")

    # Run recovery
    recovered_count = recover_incomplete_jobs()
    assert recovered_count == 1

    # Query updated state
    db_session.expire_all()
    recovered_job = db_session.query(Job).filter(Job.id == job.id).first()
    assert recovered_job.status == JobStatus.COMPLETED
    assert recovered_job.finished_at is not None

    c1 = db_session.query(Certificate).filter(Certificate.id == cert1.id).first()
    assert c1.status == CertificateStatus.SUCCESS
    # Crucial assertion: original certificate number and generated_at were NOT touched
    assert c1.certificate_number == "CERT-ORIGINAL-NUMBER"
    assert c1.generated_at == fixed_old_time

    c2 = db_session.query(Certificate).filter(Certificate.id == cert2.id).first()
    assert c2.status == CertificateStatus.SUCCESS
    assert c2.certificate_number is not None
    assert storage.exists(c2.file_path) is True

    c3 = db_session.query(Certificate).filter(Certificate.id == cert3.id).first()
    assert c3.status == CertificateStatus.SUCCESS
    assert c3.certificate_number is not None
    assert storage.exists(c3.file_path) is True
