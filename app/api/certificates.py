"""API routes for individual certificates."""

from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.bundle import file_chunk_generator
from app.services.job_service import (
    CertificateFileNotFoundError,
    CertificateNotFoundError,
    CertificateNotReadyError,
    get_certificate_for_download,
)

router = APIRouter(prefix="/api/v1/certificates", tags=["Certificates"])


@router.get(
    "/{certificate_id}/download",
    summary="Download individual certificate PDF",
)
def download_certificate(
    certificate_id: str,
    db: Annotated[Session, Depends(get_db)],
) -> StreamingResponse:
    """
    Download a single generated certificate PDF.
    Returns 404 if certificate is not found.
    Returns 409 if the certificate status is not 'success'.
    """
    try:
        file_obj, cert_filename = get_certificate_for_download(certificate_id, db)
    except CertificateNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate {certificate_id} not found",
        )
    except CertificateNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Certificate {certificate_id} status is {exc.current_status}; "
                f"only successfully generated certificates can be downloaded"
            ),
        )
    except CertificateFileNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Certificate file for {certificate_id} is missing from storage",
        )

    return StreamingResponse(
        file_chunk_generator(file_obj),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{cert_filename}"'},
    )
