"""API routes for certificate jobs."""

from typing import Annotated, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Certificate, CertificateStatus, Job, JobStatus
from app.schemas import (
    CertificateListResponse,
    JobAcceptedResponse,
    JobCreate,
    JobDetailResponse,
)
from app.services import bundle
from app.services.job_service import create_job, get_job_detail, list_job_certificates

router = APIRouter(prefix="/api/v1/jobs", tags=["Jobs"])


@router.post(
    "",
    summary="Submit bulk certificate generation job",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
def submit_job(
    payload: JobCreate,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key", max_length=255)] = None,
) -> JobAcceptedResponse:
    """
    Accepts a bulk certificate generation request.
    Validates the structure and creates the job in pending state.
    Returns 202 Accepted (or 200 OK on idempotent replays).
    """
    job, is_replay = create_job(payload=payload, db=db, idempotency_key=idempotency_key)

    status_url = f"/api/v1/jobs/{job.id}"
    response.headers["Location"] = status_url

    if is_replay:
        response.status_code = status.HTTP_200_OK

    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status,
        total=job.total_count,
        status_url=status_url,
        created_at=job.created_at,
    )


@router.get(
    "/{job_id}",
    summary="Retrieve job status and derived progress",
    response_model=JobDetailResponse,
)
def get_job(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> JobDetailResponse:
    """
    Fetch comprehensive job status with progress derived from GROUP BY status
    and failures capped at 100 with truncation indicator.
    """
    job_detail = get_job_detail(job_id=job_id, db=db)
    if not job_detail:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    return job_detail


@router.get(
    "/{job_id}/certificates",
    summary="List paginated certificates for a job",
    response_model=CertificateListResponse,
)
def get_job_certificates(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
    status: Annotated[Optional[CertificateStatus], Query(description="Filter by certificate status")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Items per page")] = 50,
    offset: Annotated[int, Query(ge=0, description="Page offset")] = 0,
) -> CertificateListResponse:
    """
    List certificates for a given job with optional status filtering and pagination.
    Includes download_url only for successful certificates.
    """
    result = list_job_certificates(
        job_id=job_id,
        db=db,
        status_filter=status,
        limit=limit,
        offset=offset,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    total, items = result
    return CertificateListResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


@router.get(
    "/{job_id}/download",
    summary="Download ZIP bundle of successful certificates",
)
def download_job_certificates_bundle(
    job_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    """
    Stream a ZIP archive containing all successful certificates for a terminal job.
    Returns 409 Conflict if the job is still pending or processing.
    """
    try:
        filename, zip_file = bundle.get_job_bundle(job_id=job_id, db=db)
    except bundle.JobNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )
    except bundle.JobNotTerminalError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} is currently {exc.current_status}; bundle is only available in terminal states",
        )

    return StreamingResponse(
        bundle.file_chunk_generator(zip_file),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
