"""Service for managing Job lifecycle, recipient validation, and persistence."""

from datetime import datetime, timezone
import logging
from typing import Any
import uuid

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import workers
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.storage import storage
from app.schemas import (
    CertificateItemResponse,
    CertificateListResponse,
    FailureDetail,
    JobCreate,
    JobDetailResponse,
    ProgressResponse,
    RecipientIn,
)

logger = logging.getLogger(__name__)


def create_job(
    payload: JobCreate,
    db: Session,
    idempotency_key: str | None = None,
) -> tuple[Job, bool]:
    """
    Validate recipients, persist Job and all Certificate rows atomically, and trigger background worker.

    Returns:
        tuple[Job, bool]: (job, is_replay) where is_replay is True if idempotency key matched an existing job.
    """
    # 1. Idempotency Check
    if idempotency_key:
        existing_job = db.query(Job).filter(Job.idempotency_key == idempotency_key).first()
        if existing_job:
            logger.info("Idempotency replay detected for key: %s (job_id: %s)", idempotency_key, existing_job.id)
            return existing_job, True

    # 2. Initialize Job
    job_id = str(uuid.uuid4())
    issue_date = payload.issue_date or datetime.now(timezone.utc).date()
    total_count = len(payload.recipients)

    job = Job(
        id=job_id,
        status=JobStatus.PENDING,
        title=payload.title,
        course_name=payload.course_name,
        issuer_name=payload.issuer_name,
        issue_date=issue_date,
        total_count=total_count,
        idempotency_key=idempotency_key,
        created_at=datetime.now(timezone.utc),
    )

    # 3. Level-2 Recipient Validation & Duplicate Detection
    seen_emails: set[str] = set()
    certificate_rows: list[Certificate] = []

    for idx, raw_record in enumerate(payload.recipients):
        cert_id = str(uuid.uuid4())
        rec_name: str | None = None
        rec_email: str | None = None
        status: CertificateStatus
        error_code: str | None = None
        error_message: str | None = None

        if not isinstance(raw_record, dict):
            status = CertificateStatus.FAILED
            error_code = "VALIDATION_ERROR"
            error_message = "Recipient payload must be a JSON object"
        else:
            try:
                validated = RecipientIn.model_validate(raw_record)
                rec_name = validated.name
                rec_email = validated.email

                if rec_email in seen_emails:
                    status = CertificateStatus.FAILED
                    error_code = "DUPLICATE_RECIPIENT"
                    error_message = f"Duplicate recipient email '{rec_email}' within job"
                else:
                    seen_emails.add(rec_email)
                    status = CertificateStatus.PENDING

            except ValidationError as exc:
                status = CertificateStatus.FAILED
                error_code = "VALIDATION_ERROR"
                # Extract descriptive field error messages
                messages = []
                for err in exc.errors():
                    field = err["loc"][-1] if err["loc"] else "field"
                    messages.append(f"{field}: {err['msg']}")
                error_message = "; ".join(messages)

                # Capture name / email if present as strings for visibility
                if isinstance(raw_record.get("name"), str):
                    rec_name = raw_record["name"][:255]
                if isinstance(raw_record.get("email"), str):
                    rec_email = raw_record["email"][:255]

        certificate_rows.append(
            Certificate(
                id=cert_id,
                job_id=job_id,
                row_index=idx,
                recipient_name=rec_name,
                recipient_email=rec_email,
                raw_input=raw_record,
                status=status,
                error_code=error_code,
                error_message=error_message,
            )
        )

    # 4. Atomic Transaction Persistence
    db.add(job)
    db.add_all(certificate_rows)
    db.commit()
    db.refresh(job)

    # 5. Submit to Worker ONLY AFTER Commit
    workers.submit(job.id)

    return job, False


def recover_incomplete_jobs() -> int:
    """
    Find jobs that were interrupted in 'pending' or 'processing' states and re-submit them.
    Reset 'processing' jobs back to 'pending' so the atomic worker claim succeeds.
    Already generated (successful or failed) certificates are never re-rendered.

    Returns:
        int: Number of recovered jobs re-submitted to worker queue.
    """
    from app.db import SessionLocal
    from sqlalchemy import update

    db = SessionLocal()
    try:
        # Revert interrupted processing jobs to pending
        db.execute(
            update(Job)
            .where(Job.status == JobStatus.PROCESSING)
            .values(status=JobStatus.PENDING)
        )
        db.commit()

        # Query all jobs ready for processing
        pending_jobs = (
            db.query(Job.id)
            .filter(Job.status == JobStatus.PENDING)
            .all()
        )
        job_ids = [row[0] for row in pending_jobs]
        logger.info("Found %d incomplete jobs to recover", len(job_ids))

        for job_id in job_ids:
            workers.submit(job_id)

        return len(job_ids)
    except Exception as exc:
        logger.exception("Failed during recovery of incomplete jobs: %s", exc)
        return 0
    finally:
        db.close()


def get_job_detail(job_id: str, db: Session) -> JobDetailResponse | None:
    """
    Retrieve comprehensive job details with progress aggregated via GROUP BY status
    and failures capped at 100 with truncation indicator.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return None

    # Derive progress counts dynamically
    status_counts = (
        db.query(Certificate.status, func.count(Certificate.id))
        .filter(Certificate.job_id == job_id)
        .group_by(Certificate.status)
        .all()
    )
    counts = {s: cnt for s, cnt in status_counts}
    succeeded = counts.get(CertificateStatus.SUCCESS, 0)
    failed = counts.get(CertificateStatus.FAILED, 0)
    pending = counts.get(CertificateStatus.PENDING, 0)
    total = job.total_count

    percent_complete = round(((succeeded + failed) / total) * 100, 1) if total > 0 else 0.0
    progress = ProgressResponse(
        total=total,
        succeeded=succeeded,
        failed=failed,
        pending=pending,
        percent_complete=percent_complete,
    )

    # Failures capped at 100
    failure_rows = (
        db.query(Certificate)
        .filter(
            Certificate.job_id == job_id,
            Certificate.status == CertificateStatus.FAILED,
        )
        .order_by(Certificate.row_index)
        .limit(101)
        .all()
    )
    failures_truncated = len(failure_rows) > 100
    capped_failures = [
        FailureDetail(
            row_index=f.row_index,
            recipient_name=f.recipient_name,
            recipient_email=f.recipient_email,
            error_code=f.error_code or "VALIDATION_ERROR",
            error_message=f.error_message or "Unknown failure",
        )
        for f in failure_rows[:100]
    ]

    return JobDetailResponse(
        job_id=job.id,
        status=job.status,
        title=job.title,
        course_name=job.course_name,
        issuer_name=job.issuer_name,
        issue_date=job.issue_date,
        progress=progress,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        failures=capped_failures,
        failures_truncated=failures_truncated,
    )


def list_job_certificates(
    job_id: str,
    db: Session,
    status_filter: CertificateStatus | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[CertificateItemResponse]] | None:
    """
    List paginated certificates for a job with optional status filtering.
    Returns (total, items) or None if job does not exist.
    """
    job_exists = db.query(Job.id).filter(Job.id == job_id).first()
    if not job_exists:
        return None

    query = db.query(Certificate).filter(Certificate.job_id == job_id)
    if status_filter:
        query = query.filter(Certificate.status == status_filter)

    total = query.count()
    rows = (
        query.order_by(Certificate.row_index)
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = [
        CertificateItemResponse(
            certificate_id=c.id,
            row_index=c.row_index,
            recipient_name=c.recipient_name,
            recipient_email=c.recipient_email,
            status=c.status,
            certificate_number=c.certificate_number,
            error_code=c.error_code,
            error_message=c.error_message,
            download_url=(
                f"/api/v1/certificates/{c.id}/download"
                if c.status == CertificateStatus.SUCCESS
                else None
            ),
            generated_at=c.generated_at,
        )
        for c in rows
    ]
    return total, items


class CertificateNotFoundError(Exception):
    """Raised when the specified certificate does not exist."""
    pass


class CertificateNotReadyError(Exception):
    """Raised when attempting download on a non-successful certificate."""
    def __init__(self, current_status: str) -> None:
        super().__init__(f"Certificate is currently {current_status}")
        self.current_status = current_status


class CertificateFileNotFoundError(Exception):
    """Raised when the physical certificate file is missing from storage."""
    pass


def get_certificate_for_download(certificate_id: str, db: Session) -> tuple[Any, str]:
    """
    Retrieve stored certificate binary file and filename for download.
    Encapsulates database lookup and status validation.
    """
    cert = db.query(Certificate).filter(Certificate.id == certificate_id).first()
    if not cert:
        raise CertificateNotFoundError(f"Certificate {certificate_id} not found")

    if cert.status != CertificateStatus.SUCCESS:
        raise CertificateNotReadyError(cert.status.value)

    if not cert.file_path or not storage.exists(cert.file_path):
        raise CertificateFileNotFoundError(
            f"Certificate file for {certificate_id} is missing from storage"
        )

    file_obj = storage.open(cert.file_path)
    filename = f"{cert.certificate_number or cert.id}.pdf"
    return file_obj, filename
