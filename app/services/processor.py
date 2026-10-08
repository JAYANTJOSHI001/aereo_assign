"""Background job processor executing per-certificate generation and failure isolation."""

from datetime import datetime, timezone
import logging
from typing import Optional
import uuid

from sqlalchemy import func, update

from app.db import SessionLocal
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.rendering import renderer
from app.rendering.renderer import CertificateData
from app.storage import storage

logger = logging.getLogger(__name__)


def classify_error(exc: Exception) -> str:
    """Classify failure as STORAGE_ERROR or RENDER_ERROR."""
    if isinstance(exc, (OSError, IOError, PermissionError)):
        return "STORAGE_ERROR"
    return "RENDER_ERROR"


def process_job(job_id: str) -> None:
    """
    Process pending certificates for a job in a dedicated session.
    Atomically claims the job from 'pending' to 'processing'.
    Executes each certificate with isolated try/except, committing per item.
    Computes final terminal status from aggregate certificate states.
    """
    db = SessionLocal()
    now_utc = datetime.now(timezone.utc)

    try:
        # 1. Atomic claim: transition from pending -> processing
        claim_stmt = (
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PENDING)
            .values(
                status=JobStatus.PROCESSING,
                started_at=func.coalesce(Job.started_at, now_utc),
            )
        )
        result = db.execute(claim_stmt)
        db.commit()

        if result.rowcount == 0:
            logger.info("Job %s was already claimed or not pending; skipping execution", job_id)
            return

        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            logger.warning("Job %s not found after claim", job_id)
            return

        logger.info("Starting processing for job_id=%s, total=%d", job.id, job.total_count)

        # 2. Iterate only PENDING certificates in row_index order
        pending_certs = (
            db.query(Certificate)
            .filter(
                Certificate.job_id == job_id,
                Certificate.status == CertificateStatus.PENDING,
            )
            .order_by(Certificate.row_index)
            .all()
        )

        for cert in pending_certs:
            try:
                # Generate unique certificate number: CERT-<YYYY>-<8 hex>
                cert_number = f"CERT-{job.issue_date.year}-{uuid.uuid4().hex[:8].upper()}"

                cert_data = CertificateData(
                    issuer_name=job.issuer_name,
                    title=job.title,
                    recipient_name=cert.recipient_name or "",
                    course_name=job.course_name,
                    issue_date=job.issue_date,
                    certificate_number=cert_number,
                )

                pdf_bytes = renderer.render_certificate(cert_data)
                rel_path = storage.save(job.id, cert.id, pdf_bytes)

                cert.status = CertificateStatus.SUCCESS
                cert.certificate_number = cert_number
                cert.file_path = rel_path
                cert.generated_at = datetime.now(timezone.utc)
                cert.error_code = None
                cert.error_message = None

            except Exception as exc:
                # Failure isolation: rollback any transaction contamination and record failure
                logger.warning(
                    "Failure generating certificate (cert_id=%s, job_id=%s): %s",
                    cert.id,
                    job.id,
                    exc,
                    exc_info=True,
                )
                db.rollback()

                # Re-query the certificate instance after rollback
                cert = db.query(Certificate).filter(Certificate.id == cert.id).first()
                if cert:
                    cert.status = CertificateStatus.FAILED
                    cert.error_code = classify_error(exc)
                    cert.error_message = str(exc)

            # Commit after every certificate
            db.commit()

        # 3. Compute final terminal status from aggregates
        status_counts = (
            db.query(Certificate.status, func.count(Certificate.id))
            .filter(Certificate.job_id == job_id)
            .group_by(Certificate.status)
            .all()
        )
        counts = {s: count for s, count in status_counts}
        succeeded = counts.get(CertificateStatus.SUCCESS, 0)
        failed = counts.get(CertificateStatus.FAILED, 0)
        pending = counts.get(CertificateStatus.PENDING, 0)

        # If any pending rows still remain (e.g. edge-case interruption), leave in processing
        if pending > 0:
            job.status = JobStatus.PROCESSING
        elif succeeded > 0 and failed == 0:
            job.status = JobStatus.COMPLETED
            job.finished_at = datetime.now(timezone.utc)
        elif succeeded > 0 and failed > 0:
            job.status = JobStatus.COMPLETED_WITH_ERRORS
            job.finished_at = datetime.now(timezone.utc)
        else:
            # 0 succeeded (all failed)
            job.status = JobStatus.FAILED
            job.finished_at = datetime.now(timezone.utc)

        db.commit()
        logger.info(
            "Finished job_id=%s with status=%s (succeeded=%d, failed=%d, pending=%d)",
            job_id,
            job.status.value,
            succeeded,
            failed,
            pending,
        )

    except Exception as exc:
        logger.exception("Fatal error while processing job_id=%s: %s", job_id, exc)
        try:
            db.rollback()
            failed_job = db.query(Job).filter(Job.id == job_id).first()
            if failed_job:
                failed_job.status = JobStatus.FAILED
                failed_job.finished_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            logger.exception("Failed to mark job %s as failed after crash", job_id)
    finally:
        db.close()
