"""Bundle service for generating streaming ZIP archives of certificates."""

import io
import re
import tempfile
from typing import BinaryIO, Generator
import zipfile
from sqlalchemy.orm import Session

from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.storage import storage


class JobNotFoundError(Exception):
    """Raised when the specified job does not exist."""
    pass


class JobNotTerminalError(Exception):
    """Raised when attempting bundle download on a non-terminal job."""
    def __init__(self, current_status: str) -> None:
        super().__init__(f"Job is currently {current_status}")
        self.current_status = current_status


def sanitize_entry_name(name: str | None, row_index: int) -> str:
    """Sanitize recipient name for safe ZIP entry filenames without path traversal risks."""
    fallback = f"recipient_{row_index}"
    raw = (name.strip() if name and name.strip() else fallback)
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", raw)[:60]
    return f"{row_index}_{cleaned}.pdf"


def create_job_zip(successful_certificates: list[Certificate]) -> BinaryIO:
    """
    Build a ZIP file in a SpooledTemporaryFile containing all successful certificates.
    Uses streaming storage reads to maintain constant memory boundaries.
    """
    spooled = tempfile.SpooledTemporaryFile(max_size=10 * 1024 * 1024, mode="w+b")
    with zipfile.ZipFile(spooled, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for cert in successful_certificates:
            if cert.file_path and storage.exists(cert.file_path):
                entry_filename = sanitize_entry_name(cert.recipient_name, cert.row_index)
                with storage.open(cert.file_path) as cert_file:
                    zf.writestr(entry_filename, cert_file.read())

    spooled.seek(0)
    return spooled


def get_job_bundle(job_id: str, db: Session) -> tuple[str, BinaryIO]:
    """
    Retrieve and build ZIP archive for a completed job.
    Enforces terminal state gating and encapsulates database queries.
    """
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        raise JobNotFoundError(f"Job {job_id} not found")

    if job.status in (JobStatus.PENDING, JobStatus.PROCESSING):
        raise JobNotTerminalError(job.status.value)

    successful_certs = (
        db.query(Certificate)
        .filter(
            Certificate.job_id == job_id,
            Certificate.status == CertificateStatus.SUCCESS,
        )
        .order_by(Certificate.row_index)
        .all()
    )

    zip_file = create_job_zip(successful_certs)
    filename = f"job_{job_id}_certificates.zip"
    return filename, zip_file


def file_chunk_generator(file_obj: BinaryIO, chunk_size: int = 64 * 1024) -> Generator[bytes, None, None]:
    """Stream chunks from a binary file object and close upon completion."""
    try:
        while True:
            chunk = file_obj.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        file_obj.close()
